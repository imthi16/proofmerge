"""Ground-truth: the new express shipping rate is applied correctly."""

from demo_shop.orders import order_total


def test_express_shipping_fee_updated():
    assert order_total([{"qty": 1, "price": 20.0}], shipping="express") == 35.0
