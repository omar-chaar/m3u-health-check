import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from ipytv import playlist
import requests
import sys
from urllib.parse import urlparse
import time
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent

try:
    sys.path.insert(0, str(PROJECT_ROOT))
    import config
except ImportError:
    config = None

logging.basicConfig(
    filename=PROJECT_ROOT / "m3u_checker.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

TIMEOUT = 10
RETRIES = 10

OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


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


def get_channel_status(url, retry_delay, diagnostics_dir=None, timeout_override=None):
    import subprocess
    import json
    import os

    headers = {"User-Agent": "VLC/3.0.11 LibVLC/3.0.11"}
    base_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=width,height,codec_name",
        "-print_format",
        "json",
        "-user_agent",
        headers["User-Agent"],
        url,
    ]
    probe_variants = [["-select_streams", "v:0"], []]
    actual_timeout = timeout_override if timeout_override else 7
    for variant in probe_variants:
        cmd = base_cmd.copy()
        cmd[3:3] = variant
        try:
            ffprobe_result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=actual_timeout,
            )
        except subprocess.TimeoutExpired:
            return "UNSTABLE"
        if ffprobe_result.returncode == 0:
            try:
                diagnostics = json.loads(ffprobe_result.stdout.decode())
                if diagnostics_dir:
                    os.makedirs(diagnostics_dir, exist_ok=True)
                    safe_url = url.replace("/", "_").replace(":", "_").replace("?", "_")
                    diag_path = os.path.join(diagnostics_dir, f"{safe_url}.json")
                    with open(diag_path, "w") as f:
                        json.dump(diagnostics, f, indent=2)
            except:
                pass
            return "ALIVE"
        if retry_delay > 0:
            time.sleep(retry_delay)
    return handle_dead_channel(url, url)


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
        alive_since_last_avg = 0

        def is_channel_alive(
            url, retry_delay, diagnostics_dir=None, timeout_override=None
        ):
            return get_channel_status(
                url, retry_delay, diagnostics_dir, timeout_override
            )

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
                        alive_since_last_avg += 1
                    results.append((ch.name, ch.url, status))
                    if status == "DEAD":
                        dead_count += 1
                    if alive_since_last_avg >= 20:
                        if timeout_samples:
                            avg = sum(timeout_samples) / len(timeout_samples)
                            avg_timeout[0] = min(30, avg + timeout_buffer)
                            logging.info(
                                f"{min(i+batch_size, total)}/{total} checked, {dead_count} dead, average timeout: {avg:.2f}s"
                            )
                        else:
                            logging.info(
                                f"{min(i+batch_size, total)}/{total} checked, {dead_count} dead"
                            )
                        alive_since_last_avg = 0

                next_batch = channels[i + batch_size : i + batch_size * 2]

        print()
        return results
    except Exception as e:
        logging.error(f"Error checking channels: {e}")
        return []


def write_channels_to_m3u(
    filename,
    channels,
    channel_map,
    extinf_map,
    extgrp_map,
    status_filter=None,
    mode="w",
):
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, mode, encoding="utf-8") as f:
        if mode == "w":
            f.write("#EXTM3U\n")
        for name, url, status in channels:
            if status_filter and status != status_filter:
                continue
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
                    getattr(ch, "group_title", None) or getattr(ch, "group", None) or ""
                )
                if group:
                    extinf_attrs.append(f'group-title="{group}"')
                extinf_str = " ".join(extinf_attrs)
                f.write(f"#EXTINF:-1 {extinf_str},{name}\n")
            if extgrp:
                f.write(f"#EXTGRP:{extgrp}\n")
            f.write(f"{url}\n")


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
        write_channels_to_m3u(
            "alive_channels.m3u", playlist_channels, channel_map, extinf_map, extgrp_map
        )
        print(
            f"Wrote {len(playlist_channels)} alive or unstable channels to {os.path.join(OUTPUT_DIR, 'alive_channels.m3u')}"
        )
    else:
        print("No alive or unstable channels found.")

    if dead_channels:
        write_channels_to_m3u(
            "dead_channels.m3u", dead_channels, channel_map, extinf_map, extgrp_map
        )
        print(
            f"Wrote {len(dead_channels)} dead channels to {os.path.join(OUTPUT_DIR, 'dead_channels.m3u')}"
        )
    else:
        print("No dead channels found.")

    if unstable_channels:
        logging.info(
            f"{len(unstable_channels)} channels were marked as UNSTABLE due to timeout."
        )
        answer = (
            input(
                f"Do you want to test the {len(unstable_channels)} unstable channels again with a longer timeout? (y/n): "
            )
            .strip()
            .lower()
        )
        if answer == "y":
            longer_timeout = 30
            retest_results = []
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_channel = {
                    executor.submit(
                        get_channel_status,
                        url,
                        retry_delay,
                        diagnostics_dir,
                        longer_timeout,
                    ): (name, url, status)
                    for name, url, status in unstable_channels
                }
                for future in as_completed(future_to_channel):
                    name, url, _ = future_to_channel[future]
                    status2 = future.result()
                    retest_results.append((name, url, status2))
            write_channels_to_m3u(
                "unstable_channels.m3u",
                [r for r in retest_results if r[2] == "ALIVE"],
                channel_map,
                extinf_map,
                extgrp_map,
            )
            write_channels_to_m3u(
                "dead_channels.m3u",
                [r for r in retest_results if r[2] != "ALIVE"],
                channel_map,
                extinf_map,
                extgrp_map,
                mode="a",
            )


if __name__ == "__main__":
    main()
