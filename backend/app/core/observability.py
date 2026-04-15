import json
import logging
from collections import defaultdict
from threading import Lock
from time import perf_counter
from typing import Any

from fastapi import Request

_metrics_lock = Lock()
_http_requests_total: dict[str, int] = defaultdict(int)
_http_request_duration_ms_total: dict[str, float] = defaultdict(float)
_http_request_duration_ms_max: dict[str, float] = defaultdict(float)
_business_events_total: dict[str, int] = defaultdict(int)

_logger = logging.getLogger("homestay.access")
if not _logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
_logger.setLevel(logging.INFO)
_logger.propagate = False


def _http_key(*, method: str, path: str, status_code: int) -> str:
    return f"{method} {path} {status_code}"


def record_http_request(*, method: str, path: str, status_code: int, duration_ms: float) -> None:
    key = _http_key(method=method, path=path, status_code=status_code)
    with _metrics_lock:
        _http_requests_total[key] += 1
        _http_request_duration_ms_total[key] += duration_ms
        _http_request_duration_ms_max[key] = max(_http_request_duration_ms_max[key], duration_ms)


def record_business_event(event: str) -> None:
    with _metrics_lock:
        _business_events_total[event] += 1


def snapshot_metrics() -> dict[str, Any]:
    with _metrics_lock:
        http_items = []
        for key, count in sorted(_http_requests_total.items()):
            total_ms = _http_request_duration_ms_total.get(key, 0.0)
            max_ms = _http_request_duration_ms_max.get(key, 0.0)
            avg_ms = (total_ms / count) if count > 0 else 0.0
            http_items.append(
                {
                    "key": key,
                    "count": count,
                    "avg_duration_ms": round(avg_ms, 3),
                    "max_duration_ms": round(max_ms, 3),
                }
            )
        business_items = [{"event": event, "count": count} for event, count in sorted(_business_events_total.items())]
    return {
        "http_requests": http_items,
        "business_events": business_items,
    }


def reset_metrics() -> None:
    with _metrics_lock:
        _http_requests_total.clear()
        _http_request_duration_ms_total.clear()
        _http_request_duration_ms_max.clear()
        _business_events_total.clear()


def log_access(*, request: Request, request_id: str, status_code: int, duration_ms: float) -> None:
    payload = {
        "event": "http_access",
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status_code": status_code,
        "duration_ms": round(duration_ms, 3),
        "client_ip": (request.client.host if request.client else "unknown"),
    }
    _logger.info(json.dumps(payload, ensure_ascii=True))


def request_timer_start() -> float:
    return perf_counter()


def request_timer_elapsed_ms(started_at: float) -> float:
    return (perf_counter() - started_at) * 1000
