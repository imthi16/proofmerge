from demo_shop.pricing import bulk_discount

def test_bulk_discount_preserves_cents():
    assert bulk_discount(105.0) == 94.5
