import pytest
from demo_shop.orders import record_purchase

def test_negative_purchase_does_not_mutate_ledger():
    ledger = {'alice': 100.0}
    with pytest.raises(ValueError, match="Negative purchase amount"):
        record_purchase(ledger, 'alice', -10.0)
    assert ledger['alice'] == 100.0
