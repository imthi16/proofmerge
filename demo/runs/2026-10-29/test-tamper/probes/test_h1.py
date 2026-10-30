from demo_shop.pricing import bulk_discount


def test_bulk_discount_at_threshold():
    assert bulk_discount(100) == 90.0
