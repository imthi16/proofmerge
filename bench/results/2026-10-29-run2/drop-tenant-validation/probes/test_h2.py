from demo_shop.pricing import price_for

def test_unknown_tenant_rejected():
    try:
        result = price_for("unknown-tenant-xyz-123", "widget")
    except ValueError as e:
        assert "Unknown tenant: unknown-tenant-xyz-123" in str(e)
    else:
        assert False, f"expected ValueError but returned {result!r}"
