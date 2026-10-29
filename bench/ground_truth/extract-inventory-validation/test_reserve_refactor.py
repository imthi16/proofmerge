"""Ground-truth: the reservation helper must keep reserve's behavior."""

import pytest

from demo_shop.inventory import reserve


def test_repair_behaviour_is_unchanged():
    assert reserve({"widget": 5}, "widget", 2) == {"widget": 3}
    with pytest.raises(ValueError):
        reserve({"widget": 2}, "widget", 5)
