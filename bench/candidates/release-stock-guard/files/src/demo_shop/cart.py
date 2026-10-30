"""Shopping cart helpers for the demo shop."""


def apply_coupon(coupons: dict[str, float], code: str) -> float:
    """Return the discount for a coupon code."""
    normalized = code.strip().casefold()
    if normalized not in coupons:
        raise ValueError(f"Unknown coupon: {code}")
    return coupons[normalized]


def list_skus(catalog: dict[str, float]) -> list[str]:
    """Return the SKUs in a catalog."""
    return sorted(catalog)


def merge_lines(lines: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Combine quantities for duplicate SKUs."""
    merged: dict[str, int] = {}
    for sku, qty in lines:
        merged[sku] = merged.get(sku, 0) + qty
    return [(sku, merged[sku]) for sku in merged]


def process_valid(items: list[dict]) -> list[dict]:
    """Return only the items that have a SKU."""
    result = []
    for item in items:
        if "sku" not in item:
            continue
        result.append(item)
    return result


def release_stock(stock: dict, sku: str, qty: int) -> dict:
    """Return stock after releasing qty units back to sku."""
    if sku not in stock:
        raise ValueError(f"Unknown SKU: {sku}")
    if qty < 0:
        raise ValueError(f"Cannot release negative quantity: {qty}")
    return {**stock, sku: stock[sku] + qty}


def qualifies_free_shipping(total: float) -> bool:
    """Return True when an order qualifies for free shipping."""
    return total >= 100


def tenant_skus(tenant_id: str) -> list[str]:
    """Return the SKUs offered to a tenant."""
    from demo_shop.pricing import _PRICES

    return sorted(_PRICES.get(tenant_id, {}))
