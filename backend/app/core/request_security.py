import ipaddress

from fastapi import HTTPException, Request, status


def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "").strip()
    if forwarded_for:
        first_ip = forwarded_for.split(",")[0].strip()
        if first_ip:
            return first_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def assert_ip_allowed(*, request: Request, allowed_ips: list[str], detail: str) -> None:
    normalized = [item.strip() for item in allowed_ips if item and item.strip()]
    if not normalized or "*" in normalized:
        return

    client_ip_raw = get_client_ip(request)
    try:
        client_ip = ipaddress.ip_address(client_ip_raw)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail) from exc

    for rule in normalized:
        try:
            if "/" in rule:
                if client_ip in ipaddress.ip_network(rule, strict=False):
                    return
            elif client_ip == ipaddress.ip_address(rule):
                return
        except ValueError:
            continue

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
