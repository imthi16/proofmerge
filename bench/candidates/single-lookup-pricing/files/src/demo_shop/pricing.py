"""Tenant-aware pricing and discount utilities."""


_PRICES = {
    "tenant-a": {"widget": 25.0},
    "tenant-b": {"widget": 35.0},
}


def price_for(tenant_id: str, sku: str) -> float:
    """Return the unit price for a tenant/sku pair."""
    table = _PRICES.get(tenant_id)
    if table is None:
        raise ValueError(f"Unknown tenant: {tenant_id}")
    price = table.get(sku)
    if price is None:
        raise ValueError(f"Unknown SKU for tenant {tenant_id}: {sku}")
    return price


def bulk_discount(total: float) -> float:
    """Return discounted total: 10% off when total >= 100."""
    if total >= 100:
        return total * 0.9
    return total
