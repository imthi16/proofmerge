"""Ground-truth: the new free-shipping threshold is applied."""

from demo_shop.cart import qualifies_free_shipping


def test_new_free_shipping_threshold():
    assert qualifies_free_shipping(150) is True
    assert qualifies_free_shipping(149) is False
    assert qualifies_free_shipping(100) is False
