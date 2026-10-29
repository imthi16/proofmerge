from demo_shop.orders import line_totals

def test_line_totals_subscriptable():
    result = line_totals([{"qty": 2, "price": 5}])
    try:
        first = result[0]
    except TypeError:
        assert False, "line_totals result is not subscriptable"
    assert first == 10
