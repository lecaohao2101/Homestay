import hashlib
import hmac
from urllib.parse import urlencode

from app.core.config import settings


def _hmac_sha256(raw: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def _canonical_query(data: dict[str, str]) -> str:
    filtered = {k: v for k, v in data.items() if v is not None}
    return urlencode(sorted(filtered.items()), doseq=False)


def create_momo_payment_url(*, txn_ref: str, amount_vnd: int, order_info: str) -> str:
    payload = {
        "partnerCode": settings.MOMO_PARTNER_CODE,
        "accessKey": settings.MOMO_ACCESS_KEY,
        "requestId": txn_ref,
        "orderId": txn_ref,
        "amount": str(amount_vnd),
        "orderInfo": order_info,
        "redirectUrl": settings.MOMO_RETURN_URL,
        "ipnUrl": settings.MOMO_IPN_URL,
        "requestType": settings.MOMO_REQUEST_TYPE,
        "extraData": "",
        "lang": "vi",
    }
    signature = _hmac_sha256(_canonical_query(payload), settings.MOMO_SECRET_KEY)
    payload["signature"] = signature
    return f"{settings.MOMO_PAYMENT_URL}?{urlencode(payload)}"


def verify_momo_signature(params: dict[str, str]) -> bool:
    received_signature = params.get("signature", "")
    if not received_signature:
        return False
    payload = {k: v for k, v in params.items() if k != "signature"}
    expected_signature = _hmac_sha256(_canonical_query(payload), settings.MOMO_SECRET_KEY)
    return hmac.compare_digest(received_signature, expected_signature)
