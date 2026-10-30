from demo_shop.pricing import price_for

def test_tenant_prices_are_independent():
    assert price_for("tenant-b", "widget") == 35.0
    assert price_for("tenant-a", "widget") == 25.0
