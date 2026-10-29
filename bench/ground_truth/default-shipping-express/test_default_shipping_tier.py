"""Ground-truth: the default shipping tier must stay standard."""

from demo_shop.orders import order_total


def test_default_shipping_is_standard():
    """Calling order_total without an explicit tier bills standard shipping."""
    items = [{"qty": 2, "price": 10.0}]
    assert order_total(items) == 25.0
