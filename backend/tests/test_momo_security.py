from app.core.payment_momo import create_momo_payment_url, verify_momo_signature


def test_momo_url_contains_signature():
    url = create_momo_payment_url(
        txn_ref="MOMOTEST123",
        amount_vnd=100000,
        order_info="test payment",
    )
    assert "signature=" in url


def test_momo_signature_verification_rejects_tamper():
    params = {
        "partnerCode": "MOMO_DEMO",
        "accessKey": "bad-key",
        "requestId": "MOMOTEST123",
        "orderId": "MOMOTEST123",
        "amount": "100000",
        "orderInfo": "test payment",
        "redirectUrl": "http://localhost:3000/payment/momo-return",
        "ipnUrl": "http://localhost:8000/api/v1/payments/momo/ipn",
        "requestType": "captureWallet",
        "extraData": "",
        "lang": "vi",
        "signature": "bad-signature",
    }
    assert verify_momo_signature(params) is False
