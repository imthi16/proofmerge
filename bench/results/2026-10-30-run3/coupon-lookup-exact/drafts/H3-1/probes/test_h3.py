import pytest
from demo_shop.cart import apply_coupon

def test_non_string_code_raises_attribute_error():
    with pytest.raises(AttributeError):
        apply_coupon({'save10': 0.1}, None)
