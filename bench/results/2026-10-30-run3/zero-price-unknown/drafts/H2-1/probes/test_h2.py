import pytest
from demo_shop.pricing import price_for

def test_unknown_sku_for_known_tenant_raises():
    with pytest.raises(ValueError, match="Unknown SKU"):
        price_for("tenant-a", "no-such-sku")
