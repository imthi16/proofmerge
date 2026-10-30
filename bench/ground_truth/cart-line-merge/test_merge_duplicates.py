"""Ground-truth: duplicate cart lines must be merged by SKU."""

from demo_shop.cart import merge_lines


def test_duplicate_skus_are_merged():
    """Two lines for the same SKU should be combined into one total quantity."""
    assert merge_lines([("widget", 2), ("widget", 3)]) == [("widget", 5)]
