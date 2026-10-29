import pytest
from demo_shop.pricing import price_for


def test_tenant_b_price_isolated():
    assert price_for("tenant-b", "widget") == 35.0


def test_unknown_tenant_rejected():
    with pytest.raises(ValueError):
        price_for("no-such-tenant", "widget")
