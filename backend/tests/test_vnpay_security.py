from app.core.payment_vnpay import create_vnpay_payment_url, verify_vnpay_signature


def test_vnpay_url_contains_signature():
    url = create_vnpay_payment_url(
        txn_ref="BOOKTEST123",
        amount_vnd=100000,
        order_info="test payment",
        ip_addr="127.0.0.1",
    )
    assert "vnp_SecureHash=" in url


def test_vnpay_signature_verification_rejects_tamper():
    params = {
        "vnp_TxnRef": "BOOKTEST123",
        "vnp_ResponseCode": "00",
        "vnp_TransactionStatus": "00",
        "vnp_TransactionNo": "999999",
        "vnp_SecureHash": "bad-signature",
    }
    assert verify_vnpay_signature(params) is False
