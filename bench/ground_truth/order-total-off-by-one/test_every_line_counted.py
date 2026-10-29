"""Ground-truth: the order total must count every line."""

from demo_shop.orders import order_total


def test_order_total_counts_the_last_line():
    """A two-line order must include the final line's amount."""
    items = [{"qty": 1, "price": 10.0}, {"qty": 2, "price": 5.0}]
    assert order_total(items, shipping="standard") == 25.0
