def test_tenant_b_widget_price_is_isolated():
    from demo_shop.pricing import price_for
    assert price_for("tenant-b", "widget") == 35.0
