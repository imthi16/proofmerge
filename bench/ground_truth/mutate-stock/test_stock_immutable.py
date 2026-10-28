"""Ground-truth: reserve must not mutate the input stock dict."""

from demo_shop.inventory import reserve


def test_reserve_returns_fresh_stock():
    """The caller's original stock dictionary must remain unchanged."""
    original = {"widget": 5}
    result = reserve(original, "widget", 3)
    assert result == {"widget": 2}
    assert original == {"widget": 5}
