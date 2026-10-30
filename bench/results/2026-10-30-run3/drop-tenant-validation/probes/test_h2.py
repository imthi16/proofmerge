import pytest
from demo_shop.pricing import price_for

def test_unknown_tenant_with_valid_sku_raises():
    with pytest.raises(ValueError, match="Unknown tenant"):
        price_for("definitely-unknown-tenant-zzz", "widget")
