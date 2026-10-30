"""Ground-truth: catalog SKU listing must stay sorted."""

from demo_shop.cart import list_skus


def test_catalog_skus_are_sorted():
    """SKUs should be returned in alphabetical order regardless of insertion order."""
    catalog = {"b": 2.0, "a": 1.0}
    assert list_skus(catalog) == ["a", "b"]
