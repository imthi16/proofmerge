import pytest

from demo_shop.orders import line_totals, order_total, promo_for, record_purchase


def test_order_total_with_cancelled_last_line():
    items = [{"qty": 1, "price": 10.0}, {"qty": 0, "price": 20.0}]
    assert order_total(items, shipping="standard") == 15.0


def test_order_total_express_with_cancelled_last_line():
    items = [{"qty": 2, "price": 4.0}, {"qty": 0, "price": 3.0}]
    assert order_total(items, shipping="express") == 23.0


def test_line_totals():
    items = [{"qty": 2, "price": 10.0}]
    assert list(line_totals(items)) == [20.0]


def test_record_purchase_adds_to_balance():
    ledger = {"alice": 40.0}
    assert record_purchase(ledger, "alice", 10.0) == 50.0
    assert ledger == {"alice": 50.0}


def test_record_purchase_unknown_customer():
    with pytest.raises(ValueError):
        record_purchase({}, "bob", 10.0)


def test_record_purchase_negative_amount():
    with pytest.raises(ValueError):
        record_purchase({"alice": 40.0}, "alice", -5.0)


def test_vip_customer_gets_vip_promotion():
    assert promo_for({"vip": True}) == "vip-10"


def test_plain_customer_gets_no_promotion():
    assert promo_for({}) == "none"
