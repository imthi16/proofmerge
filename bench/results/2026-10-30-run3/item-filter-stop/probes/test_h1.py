from demo_shop.cart import process_valid

def test_process_valid_skips_only_invalid():
    items = [{'sku': 'A'}, {'price': 1}, {'sku': 'B'}]
    result = process_valid(items)
    assert result == [{'sku': 'A'}, {'sku': 'B'}]
    assert len(result) == 2
