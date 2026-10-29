import pytest
from demo_shop.orders import record_purchase

def test_negative_purchase_leaves_ledger_unchanged():
    ledger = {'alice': 100.0}
    with pytest.raises(ValueError):
        record_purchase(ledger, 'alice', -10.0)
    assert ledger['alice'] == 100.0
