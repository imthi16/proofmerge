from demo_shop.cart import list_skus

def test_list_skus_sorted():
    assert list_skus({'b': 1.0, 'a': 2.0}) == ['a', 'b']
