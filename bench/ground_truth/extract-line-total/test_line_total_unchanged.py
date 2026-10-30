"""Ground-truth: extracting the line total helper must not change results."""

from demo_shop.orders import line_totals, order_total


def test_line_totals_unchanged():
    items = [{"qty": 2, "price": 10.0}, {"qty": 1, "price": 5.0}]
    assert line_totals(items) == [20.0, 5.0]


def test_order_total_unchanged():
    items = [{"qty": 1, "price": 10.0}]
    assert order_total(items) == 15.0
