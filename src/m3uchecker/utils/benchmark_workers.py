import time
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append('..')
import m3u_health_check


def benchmark_workers(source, retry_delay, worker_values=None, sample_size=100):
    if worker_values is None:
        cpu_count = os.cpu_count()
        worker_values = [
            cpu_count,
            cpu_count * 2,
            cpu_count * 4,
            cpu_count * 8
        ]
    
    pl = m3u_health_check.load_playlist(source)
    channels = pl.get_channels()[:sample_size]
    results = []
    
    for workers in worker_values:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    m3u_health_check.get_channel_status, ch.url, retry_delay
                )
                for ch in channels
            ]
            for future in as_completed(futures):
                future.result()
        t1 = time.time()
        elapsed = t1 - t0
        results.append((workers, elapsed))
        print(f"max_workers={workers}: {elapsed:.2f}s")
    
    best = min(results, key=lambda x: x[1])
    print(f"\nRecommended max_workers: {best[0]} (time: {best[1]:.2f}s)")
    return best[0]


if __name__ == "__main__":
    source = m3u_health_check.get_playlist_source()
    retry_delay = m3u_health_check.get_retry_delay()
    optimal = benchmark_workers(source, retry_delay)
    print(f"Using {optimal} workers for optimal performance")
