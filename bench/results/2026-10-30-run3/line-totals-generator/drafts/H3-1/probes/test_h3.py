from demo_shop.orders import line_totals

def test_empty_line_totals_is_falsy():
    result = line_totals([])
    assert not result
