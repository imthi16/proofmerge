from demo_shop.cart import (
    apply_coupon,
    list_skus,
    merge_lines,
    process_valid,
    qualifies_free_shipping,
    release_stock,
    tenant_skus,
)


def test_apply_coupon_known_code():
    assert apply_coupon({"summer10": 10.0}, "summer10") == 10.0


def test_list_skus_includes_all():
    assert set(list_skus({"a": 1.0, "b": 2.0})) == {"a", "b"}


def test_merge_lines_distinct_skus():
    assert merge_lines([("a", 1), ("b", 2)]) == [("a", 1), ("b", 2)]


def test_process_valid_keeps_items_with_sku():
    items = [{"sku": "a", "qty": 1}, {"sku": "b", "qty": 2}]
    assert process_valid(items) == items


def test_release_stock_increases_quantity():
    assert release_stock({"widget": 5}, "widget", 3) == {"widget": 8}


def test_qualifies_free_shipping_at_threshold():
    assert qualifies_free_shipping(100) is True


def test_tenant_skus_known_tenant():
    assert tenant_skus("tenant-a") == ["widget"]
