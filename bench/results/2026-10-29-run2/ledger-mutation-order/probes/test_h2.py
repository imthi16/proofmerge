from demo_shop.orders import record_purchase

def test_negative_amount_error_wins():
    try:
        record_purchase({}, "bob", -5.0)
    except ValueError as exc:
        assert "Negative purchase amount" in str(exc)
    else:
        assert False, "expected ValueError"
