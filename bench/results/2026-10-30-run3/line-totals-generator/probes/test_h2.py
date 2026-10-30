from demo_shop.orders import line_totals

def test_line_totals_reusable():
    items = [{"qty": 2, "price": 10.0}, {"qty": 1, "price": 5.0}]
    result = line_totals(items)
    assert list(result) == [20.0, 5.0]
    assert list(result) == [20.0, 5.0]
