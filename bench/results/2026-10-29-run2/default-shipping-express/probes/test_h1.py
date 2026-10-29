from demo_shop.orders import order_total

def test_default_shipping_is_standard():
    result = order_total([{"price": 10.0, "qty": 1}])
    assert result == 15.0
