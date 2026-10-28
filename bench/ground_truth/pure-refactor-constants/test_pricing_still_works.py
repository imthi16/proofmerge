"""Ground-truth: constants refactor preserves all visible behavior."""

from demo_shop.pricing import bulk_discount, price_for


def test_price_for_still_works():
    assert price_for("tenant-a", "widget") == 25.0
    assert price_for("tenant-b", "widget") == 35.0


def test_bulk_discount_still_works():
    assert bulk_discount(100) == 90.0
    assert bulk_discount(120) == 108.0
    assert bulk_discount(99) == 99.0
