import pytest
from demo_shop.pricing import price_for

def test_unknown_sku_raises_value_error():
    with pytest.raises(ValueError):
        price_for("tenant-a", "__unknown_sku_xyz_12345__")
