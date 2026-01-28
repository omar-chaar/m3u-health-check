from ipytv import playlist
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
from urllib.parse import urlparse
import time
import logging
from tqdm import tqdm

try:
    import config
except ImportError:
    config = None

logging.basicConfig(
    filename="m3u_checker.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

TIMEOUT = 10
RETRIES = 10


def get_retry_delay():
    delay = getattr(config, "RETRY_DELAY_IN_SECONDS", None) if config else None
    if delay is not None:
        return float(delay)
    user_input = input("Enter delay between retries in seconds (e.g., 0.3): ").strip()
    return float(user_input)


def get_max_workers():
    workers = getattr(config, "MAX_WORKERS", None) if config else None
    if workers is not None:
        return int(workers)
    user_input = input(
        "Enter concurrency (number of parallel checks, e.g., 50): "
    ).strip()
    return int(user_input)


def is_channel_alive(url, retry_delay):
    import subprocess

    auth = None
    if config and hasattr(config, "USERNAME") and hasattr(config, "PASSWORD"):
        auth = (config.USERNAME, config.PASSWORD)
    for attempt in range(RETRIES):
        try:
            logging.debug(f"Attempt {attempt+1}/{RETRIES} for URL: {url}")
            resp = requests.get(
                url, timeout=TIMEOUT, allow_redirects=True, auth=auth, stream=True
            )
            logging.debug(f"Status Code: {resp.status_code}, Final URL: {resp.url}")
            content_type = resp.headers.get("Content-Type", "").lower()
            logging.debug(f"Content-Type: {content_type}")
            has_chunk = False
            try:
                chunk = next(resp.iter_content(chunk_size=1024))
                if chunk:
                    has_chunk = True
            except Exception as e:
                logging.debug(f"Error reading chunk: {e}")
                has_chunk = False
            if resp.status_code == 200 and (
                any(
                    x in content_type
                    for x in [
                        "application/vnd.apple.mpegurl",
                        "application/x-mpegurl",
                        "application/octet-stream",
                        "video/",
                        "audio/",
                        "mpegurl",
                    ]
                )
                or has_chunk
            ):
                try:
                    ffprobe_cmd = [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_streams",
                        "-i",
                        url,
                    ]
                    ffprobe_result = subprocess.run(
                        ffprobe_cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=10,
                    )
                    if ffprobe_result.returncode == 0:
                        logging.debug("ffprobe succeeded, marking as ALIVE.")
                        resp.close()
                        return "ALIVE"
                    else:
                        logging.debug(
                            f"ffprobe failed: {ffprobe_result.stderr.decode(errors='ignore')}"
                        )
                except subprocess.TimeoutExpired:
                    logging.debug("ffprobe timed out.")
                except Exception as e:
                    logging.debug(f"Error running ffprobe: {e}")
            resp.close()
        except Exception as e:
            logging.debug(f"Exception during request: {e}")
        if attempt < RETRIES - 1:
            logging.debug(f"Sleeping for {retry_delay}s before next attempt.")
            time.sleep(retry_delay)
    return handle_dead_channel(url, url)


def handle_dead_channel(name, url):
    logging.info(f"Handling dead channel: {name} ({url})")
    return "DEAD"


def load_playlist(source):
    if urlparse(source).scheme in ("http", "https"):
        resp = requests.get(source)
        resp.raise_for_status()
        playlist_content = resp.text
        pl = playlist.loads(playlist_content)
        original_lines = playlist_content.splitlines()
        url_to_extinf = {}
        current_extinf = None
        for line in original_lines:
            if line.startswith("#EXTINF"):
                current_extinf = line
            elif line and not line.startswith("#") and current_extinf:
                url_to_extinf[line] = current_extinf
                current_extinf = None
        for ch in pl.get_channels():
            if ch.url in url_to_extinf:
                ch.original_extinf = url_to_extinf[ch.url]
        return pl
    else:
        with open(source, "r", encoding="utf-8") as f:
            playlist_content = f.read()
        pl = playlist.loadf(source)
        original_lines = playlist_content.splitlines()
        url_to_extinf = {}
        current_extinf = None
        for line in original_lines:
            if line.startswith("#EXTINF"):
                current_extinf = line
            elif line and not line.startswith("#") and current_extinf:
                url_to_extinf[line] = current_extinf
                current_extinf = None
        for ch in pl.get_channels():
            if ch.url in url_to_extinf:
                ch.original_extinf = url_to_extinf[ch.url]
        return pl


def get_playlist_source():
    url = getattr(config, "URL", "") if config else ""
    file_path = getattr(config, "FILE_PATH", "") if config else ""
    if url:
        return url
    if file_path:
        return file_path
    user_input = input("Enter M3U URL or file path: ").strip()
    return user_input


def check_channels(source, retry_delay, max_workers):
    pl = load_playlist(source)
    channels = pl.get_channels()
    total = len(channels)
    results = []
    dead_count = 0
    unstable_count = 0
    start_time = time.time()
    import sys

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(is_channel_alive, ch.url, retry_delay): ch
            for ch in channels
        }
        with tqdm(total=total, desc="Progress", unit="ch", ncols=80) as progress_bar:
            for idx, future in enumerate(as_completed(futures), 1):
                ch = futures[future]
                status = future.result()
                results.append((ch.name, ch.url, status))
                if status == "DEAD":
                    dead_count += 1
                    handle_dead_channel(ch.name, ch.url)
                elif status == "UNSTABLE":
                    unstable_count += 1
                progress_bar.update(1)
                progress_bar.set_postfix(
                    {"Dead": dead_count, "Unstable": unstable_count}
                )
                if idx % 10 == 0 or idx == total:
                    logging.info(
                        f"{idx}/{total} checked, {dead_count} dead, {unstable_count} unstable"
                    )
            print()
    return results


def main():
    source = get_playlist_source()
    retry_delay = get_retry_delay()
    max_workers = get_max_workers()
    pl = load_playlist(source)
    channels = pl.get_channels()
    channel_map = {ch.url: ch for ch in channels}
    extinf_map = {ch.url: getattr(ch, "original_extinf", None) for ch in channels}
    extgrp_map = {ch.url: getattr(ch, "extgrp", None) for ch in channels}
    results = check_channels(source, retry_delay, max_workers)
    for name, url, status in results:
        logging.info(f"{name}: {status} ({url})")
    alive_channels = [r for r in results if r[2] == "ALIVE"]
    if alive_channels:
        with open("alive_channels.m3u", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for name, url, status in alive_channels:
                extinf = extinf_map.get(url)
                ch = channel_map.get(url)
                if extinf:
                    extinf_parts = extinf.split(",", 1)
                    if len(extinf_parts) > 1:
                        f.write(f"{extinf_parts[0]},{name}\n")
                    else:
                        f.write(f"{extinf},{name}\n")
                else:
                    extinf_attrs = []
                    if hasattr(ch, "tvg_id") and ch.tvg_id:
                        extinf_attrs.append(f'tvg-id="{ch.tvg_id}"')
                    if hasattr(ch, "tvg_name") and ch.tvg_name:
                        extinf_attrs.append(f'tvg-name="{ch.tvg_name}"')
                    if hasattr(ch, "tvg_logo") and ch.tvg_logo:
                        extinf_attrs.append(f'tvg-logo="{ch.tvg_logo}"')
                    group = (
                        getattr(ch, "group_title", None)
                        or getattr(ch, "group", None)
                        or ""
                    )
                    if group:
                        extinf_attrs.append(f'group-title="{group}"')
                    extinf_str = " ".join(extinf_attrs)
                    f.write(f"#EXTINF:-1 {extinf_str},{name}\n")
                extgrp = extgrp_map.get(url)
                if extgrp:
                    f.write(f"#EXTGRP:{extgrp}\n")
                f.write(f"{url}\n")
        print(f"Wrote {len(alive_channels)} alive channels to alive_channels.m3u")
    else:
        print("No alive channels found.")


if __name__ == "__main__":
    main()
