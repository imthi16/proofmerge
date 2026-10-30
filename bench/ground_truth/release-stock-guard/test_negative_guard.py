"""Ground-truth: release_stock must reject negative quantities."""

import pytest

from demo_shop.cart import release_stock


def test_release_stock_rejects_negative_quantity():
    with pytest.raises(ValueError):
        release_stock({"widget": 5}, "widget", -1)
