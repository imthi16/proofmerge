from demo_shop.orders import line_totals

def test_line_totals_subscriptable():
    items = [{"qty": 2, "price": 10.0}]
    result = line_totals(items)
    try:
        value = result[0]
    except TypeError:
        assert False, "line_totals result is not subscriptable"
    assert value == 20.0
