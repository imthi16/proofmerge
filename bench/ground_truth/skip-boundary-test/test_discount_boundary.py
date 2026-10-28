"""Ground-truth: the exact threshold boundary must stay inclusive."""

from demo_shop.pricing import bulk_discount


def test_bulk_discount_at_boundary():
    """A total of exactly 100 must still receive the 10% discount."""
    assert bulk_discount(100) == 90.0
