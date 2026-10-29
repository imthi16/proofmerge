from demo_shop.orders import line_totals

def test_line_totals_len():
    items = [{'qty': 1, 'price': 2}, {'qty': 2, 'price': 3}]
    result = line_totals(items)
    try:
        n = len(result)
    except TypeError as e:
        assert False, f"len() on line_totals result raised TypeError: {e}"
    assert n == 2
