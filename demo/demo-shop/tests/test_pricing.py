from demo_shop.pricing import bulk_discount, price_for


def test_price_for_known_sku():
    assert price_for("tenant-a", "widget") == 25.0


def test_bulk_discount_at_threshold():
    assert bulk_discount(100) == 90.0


def test_bulk_discount_above_threshold():
    assert bulk_discount(120) == 108.0


def test_bulk_discount_below_threshold():
    assert bulk_discount(99) == 99.0
