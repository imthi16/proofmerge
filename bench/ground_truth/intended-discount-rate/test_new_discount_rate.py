"""Ground-truth: the new 15% discount rate is applied correctly."""

from demo_shop.pricing import bulk_discount


def test_bulk_discount_new_rate():
    assert bulk_discount(100) == 85.0
    assert bulk_discount(120) == 102.0
    assert bulk_discount(99) == 99.0
