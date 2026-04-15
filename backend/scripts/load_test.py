import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx


def _single_request(client: httpx.Client, method: str, url: str) -> tuple[float, int]:
    started = time.perf_counter()
    response = client.request(method, url)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return elapsed_ms, response.status_code


def run_load_test(*, base_url: str, path: str, method: str, total_requests: int, concurrency: int, timeout_s: float) -> dict:
    latencies: list[float] = []
    status_counts: dict[int, int] = {}
    full_url = f"{base_url.rstrip('/')}{path}"

    with httpx.Client(timeout=timeout_s) as client:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
            futures = [
                executor.submit(_single_request, client, method, full_url)
                for _ in range(max(1, total_requests))
            ]
            for fut in as_completed(futures):
                elapsed_ms, status_code = fut.result()
                latencies.append(elapsed_ms)
                status_counts[status_code] = status_counts.get(status_code, 0) + 1

    latencies.sort()
    p95_idx = int(0.95 * (len(latencies) - 1))
    p99_idx = int(0.99 * (len(latencies) - 1))

    return {
        "url": full_url,
        "method": method,
        "total_requests": len(latencies),
        "concurrency": concurrency,
        "avg_ms": round(statistics.mean(latencies), 3),
        "median_ms": round(statistics.median(latencies), 3),
        "p95_ms": round(latencies[p95_idx], 3),
        "p99_ms": round(latencies[p99_idx], 3),
        "min_ms": round(latencies[0], 3),
        "max_ms": round(latencies[-1], 3),
        "status_counts": status_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Simple HTTP load test runner")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--path", required=True, help="Path to test, e.g. /api/v1/health/live")
    parser.add_argument("--method", default="GET", help="HTTP method")
    parser.add_argument("--requests", type=int, default=200, help="Total request count")
    parser.add_argument("--concurrency", type=int, default=20, help="Concurrent workers")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout seconds")
    args = parser.parse_args()

    report = run_load_test(
        base_url=args.base_url,
        path=args.path,
        method=args.method.upper(),
        total_requests=args.requests,
        concurrency=args.concurrency,
        timeout_s=args.timeout,
    )
    print("=== Load Test Report ===")
    for key, value in report.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
