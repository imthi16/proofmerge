from demo_shop.inventory import reserve

def test_reserve_does_not_mutate_input():
    stock = {"WIDGET": 10}
    result = reserve(stock, "WIDGET", 2)
    assert result == {"WIDGET": 8}
    assert stock == {"WIDGET": 10}
    assert result is not stock
