"""Ground-truth: unknown tenants must be rejected."""

import pytest

from demo_shop.pricing import price_for


def test_unknown_tenant_rejected():
    """price_for must raise for a tenant that does not exist."""
    with pytest.raises(ValueError):
        price_for("unknown-tenant", "widget")


def test_known_tenant_price():
    """price_for must use the requested tenant's price table."""
    assert price_for("tenant-b", "widget") == 35.0
