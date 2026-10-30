from demo_shop.orders import order_total

def test_default_shipping_is_standard():
    assert order_total([{"price": 10.0, "qty": 1}]) == 15.0
