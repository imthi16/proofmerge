"""Ground-truth: tenant-aware cache must not leak prices across tenants."""

from demo_shop.pricing import price_for


def test_cross_tenant_price_isolation():
    """Each tenant must receive its own price for the same SKU."""
    price_a = price_for("tenant-a", "widget")
    price_b = price_for("tenant-b", "widget")
    assert price_a == 25.0
    assert price_b == 35.0
