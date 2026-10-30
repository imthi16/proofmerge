"""Ground-truth: coupon lookup must keep accepting padded or uppercase codes."""

from demo_shop.cart import apply_coupon


def test_padded_uppercase_coupon_still_matches():
    """A code with extra spaces or uppercase letters should still match."""
    assert apply_coupon({"summer10": 10.0}, " SUMMER10 ") == 10.0
