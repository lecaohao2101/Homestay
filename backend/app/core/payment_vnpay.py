import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import urlencode

from app.core.config import settings


def _sorted_query(data: dict[str, str]) -> str:
    filtered = {k: v for k, v in data.items() if v is not None and v != ""}
    return urlencode(sorted(filtered.items()), doseq=False)


def _hmac_sha512(raw: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha512).hexdigest()


def create_vnpay_payment_url(*, txn_ref: str, amount_vnd: int, order_info: str, ip_addr: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "vnp_Version": "2.1.0",
        "vnp_Command": "pay",
        "vnp_TmnCode": settings.VNPAY_TMN_CODE,
        "vnp_Amount": str(amount_vnd * 100),
        "vnp_CurrCode": "VND",
        "vnp_TxnRef": txn_ref,
        "vnp_OrderInfo": order_info,
        "vnp_OrderType": "other",
        "vnp_Locale": "vn",
        "vnp_ReturnUrl": settings.VNPAY_RETURN_URL,
        "vnp_IpAddr": ip_addr,
        "vnp_CreateDate": now.strftime("%Y%m%d%H%M%S"),
    }
    signed_data = _sorted_query(payload)
    secure_hash = _hmac_sha512(signed_data, settings.VNPAY_HASH_SECRET)
    payload["vnp_SecureHash"] = secure_hash
    return f"{settings.VNPAY_PAYMENT_URL}?{urlencode(payload)}"


def verify_vnpay_signature(params: dict[str, str]) -> bool:
    received_hash = params.get("vnp_SecureHash", "")
    if not received_hash:
        return False
    payload = {k: v for k, v in params.items() if k not in {"vnp_SecureHash", "vnp_SecureHashType"}}
    signed_data = _sorted_query(payload)
    expected_hash = _hmac_sha512(signed_data, settings.VNPAY_HASH_SECRET)
    return hmac.compare_digest(received_hash, expected_hash)
