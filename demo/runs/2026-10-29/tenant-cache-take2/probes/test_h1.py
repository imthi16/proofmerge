import pytest
from demo_shop.pricing import price_for

def test_unknown_tenant_still_raises_after_cache_prime():
    assert price_for("tenant-b", "widget") == 35.0
    with pytest.raises(ValueError):
        price_for("no-such-tenant", "widget")
