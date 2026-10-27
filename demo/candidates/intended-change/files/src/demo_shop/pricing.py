"""Tenant-aware pricing and discount utilities."""


_PRICES = {
    "tenant-a": {"widget": 25.0},
    "tenant-b": {"widget": 35.0},
}


def price_for(tenant_id: str, sku: str) -> float:
    """Return the unit price for a tenant/sku pair."""
    if tenant_id not in _PRICES:
        raise ValueError(f"Unknown tenant: {tenant_id}")
    table = _PRICES[tenant_id]
    if sku not in table:
        raise ValueError(f"Unknown SKU for tenant {tenant_id}: {sku}")
    return table[sku]


def bulk_discount(total: float) -> float:
    """Return discounted total: 10% off when total >= 150."""
    if total >= 150:
        return total * 0.9
    return total
