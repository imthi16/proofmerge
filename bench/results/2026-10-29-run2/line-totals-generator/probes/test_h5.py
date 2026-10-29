from demo_shop.orders import line_totals

def test_line_totals_empty_is_empty_list():
    result = line_totals([])
    assert result == []
    assert not result
