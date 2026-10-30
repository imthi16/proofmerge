def test_tenant_isolation_shared_sku():
    from demo_shop.pricing import price_for
    assert price_for("tenant-b", "widget") == 35.0
