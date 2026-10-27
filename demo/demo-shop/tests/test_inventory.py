import pytest

from demo_shop.inventory import reserve


def test_reserve_reduces_stock():
    stock = {"widget": 10}
    assert reserve(stock, "widget", 3) == {"widget": 7}


def test_reserve_unknown_sku():
    with pytest.raises(ValueError):
        reserve({}, "widget", 1)


def test_reserve_insufficient_stock():
    with pytest.raises(ValueError):
        reserve({"widget": 2}, "widget", 5)


def test_reserve_negative_quantity():
    with pytest.raises(ValueError):
        reserve({"widget": 2}, "widget", -1)
