"""Ground-truth: the comprehension rewrite must keep line totals intact."""

from demo_shop.orders import line_totals


def test_line_totals_still_a_list():
    assert line_totals([{"qty": 1, "price": 10.0}, {"qty": 2, "price": 5.0}]) == [10.0, 10.0]
