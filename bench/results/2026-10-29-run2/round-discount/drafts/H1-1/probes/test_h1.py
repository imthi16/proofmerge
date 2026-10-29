import pytest
from demo_shop.pricing import bulk_discount

def test_bulk_discount_101_exact():
    assert bulk_discount(101) == pytest.approx(90.9)
