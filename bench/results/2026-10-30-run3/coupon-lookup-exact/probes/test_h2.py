def test_apply_coupon_strips_whitespace():
    from demo_shop.cart import apply_coupon
    try:
        result = apply_coupon({"save10": 0.1}, "  save10  ")
    except ValueError:
        assert False, "apply_coupon raised ValueError for whitespace-padded code"
    assert result == 0.1
