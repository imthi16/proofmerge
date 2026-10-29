from demo_shop.pricing import bulk_discount


def test_bulk_discount_at_100():
    assert bulk_discount(100) == 90.0
