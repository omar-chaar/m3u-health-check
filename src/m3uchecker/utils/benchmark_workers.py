import logging
import os
import threading
import time

from m3uchecker.health_check import check_channels

BENCHMARK_WORKERS = [
    int(x.strip())
    for x in os.getenv("BENCHMARK_WORKERS", "4,8,10,12,16,20,40,60").split(",")
    if x.strip().isdigit()
]
BENCHMARK_CACHE_SECONDS = int(os.getenv("BENCHMARK_CACHE_SECONDS", "3600"))

_benchmark_lock = threading.Lock()
_last_benchmark_ts = 0.0
_last_best_workers = None


def get_last_best_workers(default):
    return _last_best_workers if _last_best_workers is not None else default


def get_fastest_workers(source, retry_delay, max_workers, diagnostics_dir):
    global _last_benchmark_ts, _last_best_workers

    now = time.time()
    if (
        _last_best_workers is not None
        and (now - _last_benchmark_ts) < BENCHMARK_CACHE_SECONDS
    ):
        return _last_best_workers

    with _benchmark_lock:
        now = time.time()
        if (
            _last_best_workers is not None
            and (now - _last_benchmark_ts) < BENCHMARK_CACHE_SECONDS
        ):
            return _last_best_workers

        worker_values = sorted(set(BENCHMARK_WORKERS + [max_workers]))
        best_workers = max_workers
        best_time = float("inf")

        for workers in worker_values:
            try:
                start = time.perf_counter()
                check_channels(source, retry_delay, workers, diagnostics_dir)
                elapsed = time.perf_counter() - start
                logging.info(f"Benchmark workers={workers} took {elapsed:.2f}s")
                if elapsed < best_time:
                    best_time = elapsed
                    best_workers = workers
            except Exception:
                logging.exception(f"Benchmark failed for workers={workers}")

        _last_best_workers = best_workers
        _last_benchmark_ts = time.time()
        logging.info(f"Selected fastest workers={best_workers}")
        return best_workers
