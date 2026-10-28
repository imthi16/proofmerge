"""Ground-truth: bulk discount must preserve exact float results."""

from demo_shop.pricing import bulk_discount


def test_bulk_discount_preserves_float():
    """A non-round total should keep its fractional discount result."""
    assert bulk_discount(111) == 99.9
