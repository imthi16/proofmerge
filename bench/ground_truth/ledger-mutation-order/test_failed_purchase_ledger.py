"""Ground-truth: a rejected purchase must not touch the customer ledger."""

import pytest

from demo_shop.orders import record_purchase


def test_failed_purchase_leaves_ledger_unchanged():
    """A negative amount must be rejected before the ledger is updated."""
    ledger = {"alice": 40.0}
    with pytest.raises(ValueError):
        record_purchase(ledger, "alice", -5.0)
    assert ledger == {"alice": 40.0}
