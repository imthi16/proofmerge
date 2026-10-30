from demo_shop.cart import merge_lines

def test_merge_lines_sums_duplicates():
    assert merge_lines([('A', 2), ('A', 3)]) == [('A', 5)]
