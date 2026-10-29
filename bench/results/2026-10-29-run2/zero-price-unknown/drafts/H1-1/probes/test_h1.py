import pytest
from demo_shop.pricing import price_for

def test_unknown_tenant_raises_value_error():
    with pytest.raises(ValueError):
        price_for('__unknown_tenant_xyz_12345__', 'anything')
