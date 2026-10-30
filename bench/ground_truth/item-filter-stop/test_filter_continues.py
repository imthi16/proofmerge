"""Ground-truth: item filtering must skip invalid items and keep the rest."""

from demo_shop.cart import process_valid


def test_invalid_item_does_not_drop_later_items():
    """An item without a SKU should be ignored, not stop processing."""
    items = [{"qty": 1}, {"sku": "widget", "qty": 2}]
    assert process_valid(items) == [{"sku": "widget", "qty": 2}]
