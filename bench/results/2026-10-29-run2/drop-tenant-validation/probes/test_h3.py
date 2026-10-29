import pytest
from demo_shop.pricing import price_for

def test_unknown_sku_error_mentions_tenant():
    tenant = "tenant-a"
    sku = "no-such-sku-xyz-123"
    with pytest.raises(ValueError) as exc_info:
        price_for(tenant, sku)
    msg = str(exc_info.value)
    assert tenant in msg
    assert sku in msg
