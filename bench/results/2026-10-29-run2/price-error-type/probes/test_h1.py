from demo_shop import pricing


def test_unknown_sku_raises_value_error():
    try:
        pricing.price_for("tenant-a", "NO_SUCH_SKU_XYZ")
    except ValueError as exc:
        assert isinstance(exc, ValueError)
        assert not isinstance(exc, LookupError)
    except LookupError:
        assert False, "expected ValueError, got LookupError"
    except Exception:
        assert False, "expected ValueError, got different exception"
    else:
        assert False, "expected ValueError for unknown SKU"
