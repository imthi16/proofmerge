"""Ground-truth: a customer qualifying for both promotions gets the VIP one."""

from demo_shop.orders import promo_for


def test_vip_takes_priority():
    """VIP must win even when the newsletter rule matches first."""
    assert promo_for({"vip": True, "newsletter": True}) == "vip-10"
