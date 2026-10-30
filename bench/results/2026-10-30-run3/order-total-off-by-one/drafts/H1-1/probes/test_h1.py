from demo_shop.orders import order_total

def test_order_total_includes_last_item():
    items = [{"qty": 2, "price": 10.0}, {"qty": 1, "price": 5.0}]
    assert order_total(items, shipping="standard") == 30.0
