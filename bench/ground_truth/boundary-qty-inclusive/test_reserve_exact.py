"""Ground-truth: reserving the exact available quantity must succeed."""

from demo_shop.inventory import reserve


def test_reserve_exact_stock():
    """A request for exactly the available stock should leave zero quantity."""
    assert reserve({"widget": 5}, "widget", 5) == {"widget": 0}
