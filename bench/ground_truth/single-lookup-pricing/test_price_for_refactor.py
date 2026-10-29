"""Ground-truth: the one-lookup rewrite must keep every price answer."""

import pytest

from demo_shop.pricing import price_for


def test_price_for_unchanged():
    assert price_for("tenant-a", "widget") == 25.0
    assert price_for("tenant-b", "widget") == 35.0
    with pytest.raises(ValueError):
        price_for("unknown-tenant", "widget")
