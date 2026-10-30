import pytest
from demo_shop.orders import record_purchase

def test_unknown_customer_negative_amount_prefers_amount_error():
    with pytest.raises(ValueError, match="Negative purchase amount"):
        record_purchase({}, "bob", -5.0)
