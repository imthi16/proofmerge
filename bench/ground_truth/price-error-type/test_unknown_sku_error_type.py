"""Ground-truth: unknown SKUs must keep raising ValueError."""

import pytest

from demo_shop.pricing import price_for


def test_unknown_sku_raises_valueerror():
    """price_for must keep its ValueError for an unknown SKU."""
    with pytest.raises(ValueError):
        price_for("tenant-a", "gadget")
