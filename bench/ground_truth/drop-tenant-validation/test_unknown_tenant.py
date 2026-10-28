"""Ground-truth: unknown tenants must be rejected."""

import pytest

from demo_shop.pricing import price_for


def test_unknown_tenant_rejected():
    """price_for must raise for a tenant that does not exist."""
    with pytest.raises(ValueError):
        price_for("unknown-tenant", "widget")
