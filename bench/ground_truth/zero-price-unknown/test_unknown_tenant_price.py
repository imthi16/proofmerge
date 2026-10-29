"""Ground-truth: unknown tenants must not be answered with a price."""

import pytest

from demo_shop.pricing import price_for


def test_unknown_tenant_still_raises():
    """An unknown tenant must keep raising ValueError, not a price."""
    with pytest.raises(ValueError):
        price_for("tenant-z", "widget")
