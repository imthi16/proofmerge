from demo_shop.inventory import reserve

def test_exact_stock_reservation_allowed():
    try:
        result = reserve({"SKU1": 5}, "SKU1", 5)
    except ValueError:
        assert False, "reserve of exact available stock should succeed"
    assert result["SKU1"] == 0
    assert result == {"SKU1": 0}
