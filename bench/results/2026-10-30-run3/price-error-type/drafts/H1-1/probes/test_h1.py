from demo_shop.pricing import price_for

def test_unknown_sku_raises_value_error():
    try:
        price_for("tenant-a", "no-such-sku-123")
    except ValueError as exc:
        assert "Unknown SKU" in str(exc)
    except Exception as exc:
        assert False, "expected ValueError but got " + str(type(exc)) + " " + str(exc)
    else:
        assert False, "expected ValueError for unknown SKU"
