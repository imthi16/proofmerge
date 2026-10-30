"""Ground-truth: precomputed tenant SKU lists must return the same results."""

from demo_shop.cart import tenant_skus


def test_tenant_skus_unchanged():
    assert tenant_skus("tenant-a") == ["widget"]
    assert tenant_skus("tenant-b") == ["widget"]
