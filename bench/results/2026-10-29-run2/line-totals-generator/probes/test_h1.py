from demo_shop.orders import line_totals

def test_line_totals_returns_list():
    result = line_totals([{"qty": 2, "price": 5}, {"qty": 3, "price": 5}])
    assert result == [10, 15]
    assert isinstance(result, list)
