import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from ipytv import playlist
import requests
import sys
from urllib.parse import urlparse
import time

try:
    import config
except ImportError:
    config = None

logging.basicConfig(
    filename="m3u_checker.log",
    level=logging.INFO,
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


def handle_dead_channel(name, url):
    try:
        logging.info(f"Handling dead channel: {name} ({url})")
        return "DEAD"
    except Exception as e:
        logging.error(f"Error handling dead channel: {e}")
        return "ERROR"


def load_playlist(source):
    try:
        if urlparse(source).scheme in ("http", "https"):
            resp = requests.get(source)
            resp.raise_for_status()
            playlist_content = resp.text
            pl = playlist.loads(playlist_content)
            original_lines = playlist_content.splitlines()
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
            ch.original_extinf = url_to_extinf.get(
                ch.url, getattr(ch, "original_extinf", None)
            )
            ch.extgrp = getattr(ch, "extgrp", None)
        return pl
    except FileNotFoundError:
        logging.error(f"File not found: {source}")
    except Exception as e:
        logging.error(f"Error loading playlist: {e}")


def get_playlist_source():
    try:
        url = getattr(config, "URL", "") if config else ""
        file_path = getattr(config, "FILE_PATH", "") if config else ""
        if url:
            return url
        if file_path:
            return file_path
        user_input = input("Enter M3U URL or file path: ").strip()
        return user_input
    except Exception as e:
        logging.error(f"Error getting playlist source: {e}")
        return None


def check_channels(source, retry_delay, max_workers, diagnostics_dir=None):
    try:
        pl = load_playlist(source)
        if not pl:
            return []
        channels = pl.get_channels()
        total = len(channels)
        results = []
        dead_count = 0
        timeout_samples = []
        avg_timeout = [8]
        timeout_buffer = 3
        initial_timeout = 10
        batch_size = min(25, max(10, total // 10))

        def is_channel_alive(
            url, retry_delay, diagnostics_dir=None, timeout_override=None
        ):
            import subprocess
            import json
            import os

            try:
                headers = {"User-Agent": "VLC/3.0.11 LibVLC/3.0.11"}
                ffprobe_cmd = [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height,codec_name",
                    "-print_format",
                    "json",
                    "-user_agent",
                    headers["User-Agent"],
                    url,
                ]

                actual_timeout = timeout_override if timeout_override else 7
                ffprobe_result = subprocess.run(
                    ffprobe_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=actual_timeout,
                )

                if ffprobe_result.returncode == 0:
                    try:
                        diagnostics = json.loads(ffprobe_result.stdout.decode())
                        if diagnostics_dir:
                            os.makedirs(diagnostics_dir, exist_ok=True)
                            safe_url = (
                                url.replace("/", "_")
                                .replace(":", "_")
                                .replace("?", "_")
                            )
                            diag_path = os.path.join(
                                diagnostics_dir, f"{safe_url}.json"
                            )
                            with open(diag_path, "w") as f:
                                json.dump(diagnostics, f, indent=2)
                    except:
                        pass
                    return "ALIVE"

                if retry_delay > 0:
                    time.sleep(retry_delay)

                ffprobe_cmd[3:5] = []
                ffprobe_result = subprocess.run(
                    ffprobe_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=actual_timeout,
                )

                if ffprobe_result.returncode == 0:
                    return "ALIVE"
                return handle_dead_channel(url, url)
            except:
                return "DEAD"

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for i in range(0, total, batch_size):
                batch_channels = channels[i : i + batch_size]
                futures = {
                    executor.submit(
                        is_channel_alive,
                        ch.url,
                        retry_delay,
                        diagnostics_dir,
                        None,
                    ): ch
                    for ch in batch_channels
                }

                for future in as_completed(futures):
                    ch = futures[future]
                    t0 = time.time()
                    status = future.result()
                    t1 = time.time()
                    if status == "ALIVE":
                        timeout_samples.append(t1 - t0)
                        if len(timeout_samples) >= 5:
                            avg = sum(timeout_samples) / len(timeout_samples)
                            avg_timeout[0] = min(30, avg + timeout_buffer)
                    results.append((ch.name, ch.url, status))
                    if status == "DEAD":
                        dead_count += 1

                if timeout_samples:
                    logging.info(
                        f"{min(i+batch_size, total)}/{total} checked, {dead_count} dead, timeout: {avg_timeout[0]:.2f}s"
                    )
                else:
                    logging.info(
                        f"{min(i+batch_size, total)}/{total} checked, {dead_count} dead"
                    )

                next_batch = channels[i + batch_size : i + batch_size * 2]

        print()
        return results
    except Exception as e:
        logging.error(f"Error checking channels: {e}")
        return []


def main():
    source = get_playlist_source()
    if not source:
        print("Invalid playlist source.")
        return
    retry_delay = get_retry_delay()
    max_workers = get_max_workers()
    diagnostics_dir = "diagnostics"
    pl = load_playlist(source)
    if not pl:
        print("Failed to load playlist.")
        return
    channels = pl.get_channels()
    channel_map = {ch.url: ch for ch in channels}
    extinf_map = {ch.url: getattr(ch, "original_extinf", None) for ch in channels}
    extgrp_map = {ch.url: getattr(ch, "extgrp", None) for ch in channels}
    results = check_channels(source, retry_delay, max_workers, diagnostics_dir)
    for name, url, status in results:
        logging.info(f"{name}: {status} ({url})")
    alive_channels = [r for r in results if r[2] == "ALIVE"]
    unstable_channels = [r for r in results if r[2] == "UNSTABLE"]
    dead_channels = [r for r in results if r[2] == "DEAD"]
    playlist_channels = alive_channels + unstable_channels
    if playlist_channels:
        with open("alive_channels.m3u", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for name, url, status in playlist_channels:
                ch = channel_map.get(url)
                extinf = extinf_map.get(url)
                extgrp = extgrp_map.get(url)
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
                if extgrp:
                    f.write(f"#EXTGRP:{extgrp}\n")
                f.write(f"{url}\n")
        print(
            f"Wrote {len(playlist_channels)} alive or unstable channels to alive_channels.m3u"
        )
    else:
        print("No alive or unstable channels found.")

    if dead_channels:
        with open("dead_channels.m3u", "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for name, url, status in dead_channels:
                ch = channel_map.get(url)
                extinf = extinf_map.get(url)
                extgrp = extgrp_map.get(url)
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
                if extgrp:
                    f.write(f"#EXTGRP:{extgrp}\n")
                f.write(f"{url}\n")
        print(f"Wrote {len(dead_channels)} dead channels to dead_channels.m3u")
    else:
        print("No dead channels found.")


if __name__ == "__main__":
    main()
