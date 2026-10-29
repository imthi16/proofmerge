"""Ground-truth: per-line totals must come back as a list."""

from demo_shop.orders import line_totals


def test_line_totals_returns_a_list():
    """Callers index and compare line totals as a list, in order."""
    items = [{"qty": 1, "price": 10.0}, {"qty": 2, "price": 5.0}]
    assert line_totals(items) == [10.0, 10.0]
