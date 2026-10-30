from demo_shop.cart import apply_coupon

def test_apply_coupon_case_insensitive():
    try:
        discount = apply_coupon({"save10": 0.1}, "SAVE10")
    except ValueError:
        assert False, "base should normalize 'SAVE10' to 'save10' and return 0.1"
    assert discount == 0.1
