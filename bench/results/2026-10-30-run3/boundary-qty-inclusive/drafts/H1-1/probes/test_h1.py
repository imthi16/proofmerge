def test_exact_quantity_reservation_allowed():
    from demo_shop.inventory import reserve
    try:
        result = reserve({"WIDGET": 5}, "WIDGET", 5)
    except ValueError:
        assert False, "reserve raised ValueError for qty == available, expected success"
    assert result == {"WIDGET": 0}
