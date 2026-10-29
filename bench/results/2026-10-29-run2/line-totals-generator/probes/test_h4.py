from demo_shop.orders import line_totals

def test_line_totals_reusable():
    items = [{"qty": 1, "price": 2}, {"qty": 2, "price": 3}]
    result = line_totals(items)
    assert list(result) == [2, 6]
    assert list(result) == [2, 6]
