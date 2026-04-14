import logging
import os
import threading

from m3uchecker.health_check import check_channels
from m3uchecker.utils.benchmark_workers import (
    get_fastest_workers as _get_fastest_workers,
    get_last_best_workers as _get_last_best_workers,
)

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
FINAL_PLAYLIST_FILE = os.path.join(OUTPUT_DIR, "final_channels.m3u")
CACHE_MAX_AGE_SECONDS = int(os.getenv("CACHE_MAX_AGE_SECONDS", str(24 * 60 * 60)))

PLAYLIST_SOURCE = os.getenv("PLAYLIST_SOURCE", "")
REFRESH_RETRY_DELAY = float(os.getenv("REFRESH_RETRY_DELAY", "0.3"))
REFRESH_MAX_WORKERS = int(os.getenv("REFRESH_MAX_WORKERS", "10"))
REFRESH_DIAGNOSTICS_DIR = os.getenv("REFRESH_DIAGNOSTICS_DIR", "diagnostics")

os.makedirs(OUTPUT_DIR, exist_ok=True)
_refreshing = threading.Event()


def get_last_best_workers():
    return _get_last_best_workers(REFRESH_MAX_WORKERS)


def _build_m3u_from_results(results):
    lines = ["#EXTM3U"]
    for _, url, status, extinf in results:
        if status == "ALIVE":
            if extinf:
                lines.append(extinf)
            lines.append(url)
    return "\n".join(lines) + "\n"


def _refresh_cached_playlist():
    try:
        if not PLAYLIST_SOURCE:
            logging.warning("PLAYLIST_SOURCE is empty; skipping refresh.")
            return

        workers = _get_fastest_workers(
            PLAYLIST_SOURCE,
            REFRESH_RETRY_DELAY,
            REFRESH_MAX_WORKERS,
            REFRESH_DIAGNOSTICS_DIR,
        )
        results = check_channels(
            PLAYLIST_SOURCE,
            REFRESH_RETRY_DELAY,
            workers,
            REFRESH_DIAGNOSTICS_DIR,
        )
        content = _build_m3u_from_results(results)

        tmp_file = f"{FINAL_PLAYLIST_FILE}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_file, FINAL_PLAYLIST_FILE)

        logging.info(f"Cached playlist refreshed with workers={workers}.")
    except Exception:
        logging.exception("Failed to refresh cached playlist.")
    finally:
        _refreshing.clear()


def trigger_refresh_on_background():
    if _refreshing.is_set():
        return False
    _refreshing.set()
    threading.Thread(target=_refresh_cached_playlist, daemon=True).start()
    return True


def trigger_refresh_async():
    return trigger_refresh_on_background()
