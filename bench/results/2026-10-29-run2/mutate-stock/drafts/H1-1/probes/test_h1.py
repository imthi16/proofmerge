from demo_shop.inventory import reserve

def test_reserve_does_not_mutate_caller_dict():
    stock = {'A': 10}
    r1 = reserve(stock, 'A', 2)
    assert r1 == {'A': 8}
    assert stock == {'A': 10}
    assert r1 is not stock
    r2 = reserve(stock, 'A', 2)
    assert r2 == {'A': 8}
    assert r2 == r1
