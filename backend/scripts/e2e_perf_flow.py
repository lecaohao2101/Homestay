import argparse
import json
import statistics
import time
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import httpx


def _timed_request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    form_body: dict[str, str] | None = None,
) -> tuple[httpx.Response, float]:
    started = time.perf_counter()
    response = client.request(method, path, headers=headers, json=json_body, data=form_body)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return response, elapsed_ms


def _login(client: httpx.Client, *, email: str, password: str) -> tuple[str, float]:
    response, elapsed_ms = _timed_request(
        client,
        "POST",
        "/api/v1/auth/login",
        form_body={"username": email, "password": password},
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    return token, elapsed_ms


def _register_or_continue(client: httpx.Client, *, email: str, password: str, full_name: str) -> None:
    response, _ = _timed_request(
        client,
        "POST",
        "/api/v1/auth/register",
        json_body={"email": email, "password": password, "full_name": full_name},
    )
    if response.status_code in (200, 201):
        return
    # Existing account may return 400 in this project.
    if response.status_code == 400:
        return
    response.raise_for_status()


def _get_user_id_by_email(client: httpx.Client, *, admin_token: str, email: str) -> str:
    response, _ = _timed_request(
        client,
        "GET",
        "/api/v1/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    response.raise_for_status()
    for item in response.json().get("items", []):
        if item.get("email", "").strip().lower() == email.strip().lower():
            return item["id"]
    raise RuntimeError(f"Cannot find user id for email: {email}")


def _ensure_host_role(client: httpx.Client, *, admin_token: str, user_id: str) -> None:
    response, _ = _timed_request(
        client,
        "PATCH",
        f"/api/v1/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json_body={"role": "host", "is_active": True},
    )
    response.raise_for_status()


def _bootstrap_property_room(
    client: httpx.Client,
    *,
    host_token: str,
    city: str,
    country: str,
    base_price: int,
    quantity: int,
) -> tuple[str, str]:
    property_name_suffix = uuid4().hex[:8]
    create_property_resp, _ = _timed_request(
        client,
        "POST",
        "/api/v1/properties",
        headers={"Authorization": f"Bearer {host_token}"},
        json_body={
            "name": f"Perf Homestay {property_name_suffix}",
            "description": "Performance benchmark property",
            "address": "100 Perf Street",
            "city": city,
            "country": country,
        },
    )
    create_property_resp.raise_for_status()
    property_id = create_property_resp.json()["id"]

    create_room_resp, _ = _timed_request(
        client,
        "POST",
        f"/api/v1/properties/{property_id}/rooms",
        headers={"Authorization": f"Bearer {host_token}"},
        json_body={
            "name": "Perf Room",
            "capacity": 2,
            "price_per_night": base_price,
            "quantity": quantity,
        },
    )
    create_room_resp.raise_for_status()
    room_id = create_room_resp.json()["id"]
    return property_id, room_id


def _upsert_availability_for_range(
    client: httpx.Client,
    *,
    host_token: str,
    property_id: str,
    room_id: str,
    start_date: date,
    end_date: date,
    available_units: int,
    price_per_night: int,
) -> None:
    response, _ = _timed_request(
        client,
        "PUT",
        f"/api/v1/properties/{property_id}/rooms/{room_id}/availability",
        headers={"Authorization": f"Bearer {host_token}"},
        json_body={
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "available_units": available_units,
            "price_per_night": price_per_night,
        },
    )
    response.raise_for_status()


def _run_single_iteration(
    client: httpx.Client,
    *,
    guest_token: str,
    property_id: str,
    room_id: str,
    provider: str,
    check_in: date,
    check_out: date,
    iteration_index: int,
) -> dict[str, Any]:
    step_latency: dict[str, float] = {}

    providers_resp, providers_ms = _timed_request(client, "GET", "/api/v1/payments/providers")
    providers_resp.raise_for_status()
    step_latency["providers_ms"] = providers_ms

    search_resp, search_ms = _timed_request(
        client,
        "GET",
        f"/api/v1/search/properties?city=Danang&country=Vietnam&check_in={check_in.isoformat()}&check_out={check_out.isoformat()}",
    )
    search_resp.raise_for_status()
    step_latency["search_ms"] = search_ms

    idem_key = f"perf-{iteration_index}-{uuid4().hex[:12]}"
    booking_resp, booking_ms = _timed_request(
        client,
        "POST",
        "/api/v1/bookings",
        headers={"Authorization": f"Bearer {guest_token}", "X-Idempotency-Key": idem_key},
        json_body={
            "property_id": property_id,
            "room_id": room_id,
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "units": 1,
        },
    )
    booking_resp.raise_for_status()
    booking_id = booking_resp.json()["id"]
    step_latency["booking_create_ms"] = booking_ms

    create_payment_path = f"/api/v1/payments/{provider}/create"
    payment_resp, payment_ms = _timed_request(
        client,
        "POST",
        create_payment_path,
        headers={"Authorization": f"Bearer {guest_token}"},
        json_body={"booking_id": booking_id},
    )
    payment_resp.raise_for_status()
    step_latency["payment_create_ms"] = payment_ms

    return {
        "ok": True,
        "booking_id": booking_id,
        "payment_id": payment_resp.json().get("payment_id"),
        "txn_ref": payment_resp.json().get("txn_ref"),
        "step_latency_ms": step_latency,
        "elapsed_ms": sum(step_latency.values()),
    }


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    index = int(p * (len(sorted_values) - 1))
    return sorted_values[index]


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E performance flow for auth + booking + payment create")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--admin-email", required=True, help="Admin account email")
    parser.add_argument("--admin-password", required=True, help="Admin account password")
    parser.add_argument("--scenario-prefix", default="perf", help="Prefix for generated users")
    parser.add_argument("--password", default="StrongPassw0rd!123", help="Password for generated users")
    parser.add_argument("--provider", choices=["vnpay", "momo"], default="vnpay", help="Payment provider")
    parser.add_argument("--iterations", type=int, default=20, help="Measured iterations")
    parser.add_argument("--warmup", type=int, default=3, help="Warmup iterations")
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout seconds")
    parser.add_argument("--output", default="", help="Optional JSON report output path")
    args = parser.parse_args()

    now = int(time.time())
    host_email = f"{args.scenario_prefix}.host.{now}@example.com"
    guest_email = f"{args.scenario_prefix}.guest.{now}@example.com"

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=args.timeout) as client:
        _register_or_continue(client, email=host_email, password=args.password, full_name="Perf Host")
        _register_or_continue(client, email=guest_email, password=args.password, full_name="Perf Guest")

        admin_token, admin_login_ms = _login(client, email=args.admin_email, password=args.admin_password)
        host_user_id = _get_user_id_by_email(client, admin_token=admin_token, email=host_email)
        _ensure_host_role(client, admin_token=admin_token, user_id=host_user_id)

        host_token, host_login_ms = _login(client, email=host_email, password=args.password)
        guest_token, guest_login_ms = _login(client, email=guest_email, password=args.password)

        property_id, room_id = _bootstrap_property_room(
            client,
            host_token=host_token,
            city="Danang",
            country="Vietnam",
            base_price=1000000,
            quantity=max(5, args.iterations + args.warmup + 2),
        )

        warmup_total = max(0, args.warmup)
        measured_total = max(1, args.iterations)
        measured_results: list[dict[str, Any]] = []

        for idx in range(warmup_total + measured_total):
            # Use distinct date windows to avoid contention with previous bookings.
            check_in = date.today() + timedelta(days=60 + idx * 3)
            check_out = check_in + timedelta(days=2)
            _upsert_availability_for_range(
                client,
                host_token=host_token,
                property_id=property_id,
                room_id=room_id,
                start_date=check_in,
                end_date=check_out,
                available_units=3,
                price_per_night=1000000,
            )
            result = _run_single_iteration(
                client,
                guest_token=guest_token,
                property_id=property_id,
                room_id=room_id,
                provider=args.provider,
                check_in=check_in,
                check_out=check_out,
                iteration_index=idx,
            )
            if idx >= warmup_total:
                measured_results.append(result)

    elapsed_values = sorted(item["elapsed_ms"] for item in measured_results)
    success_count = sum(1 for item in measured_results if item["ok"])
    summary = {
        "avg_ms": round(statistics.mean(elapsed_values), 3),
        "median_ms": round(statistics.median(elapsed_values), 3),
        "p95_ms": round(_percentile(elapsed_values, 0.95), 3),
        "p99_ms": round(_percentile(elapsed_values, 0.99), 3),
        "min_ms": round(elapsed_values[0], 3),
        "max_ms": round(elapsed_values[-1], 3),
        "success_rate": round(success_count / len(measured_results), 6),
    }

    step_keys = ["providers_ms", "search_ms", "booking_create_ms", "payment_create_ms"]
    step_summary: dict[str, dict[str, float]] = {}
    for key in step_keys:
        values = sorted(item["step_latency_ms"][key] for item in measured_results)
        step_summary[key] = {
            "avg_ms": round(statistics.mean(values), 3),
            "p95_ms": round(_percentile(values, 0.95), 3),
        }

    report = {
        "scenario": "auth_booking_payment_create",
        "provider": args.provider,
        "base_url": args.base_url,
        "iterations": measured_total,
        "warmup": warmup_total,
        "bootstrap": {
            "admin_login_ms": round(admin_login_ms, 3),
            "host_login_ms": round(host_login_ms, 3),
            "guest_login_ms": round(guest_login_ms, 3),
            "property_id": property_id,
            "room_id": room_id,
        },
        "summary": summary,
        "steps": step_summary,
    }

    output_json = json.dumps(report, ensure_ascii=True, indent=2)
    print(output_json)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)


if __name__ == "__main__":
    main()
