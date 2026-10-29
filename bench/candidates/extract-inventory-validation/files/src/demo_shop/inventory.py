"""Inventory reservation utilities."""


def _validate_request(stock: dict, sku: str, qty: int) -> int:
    """Validate a reservation request and return the available quantity."""
    if sku not in stock:
        raise ValueError(f"Unknown SKU: {sku}")
    available = stock[sku]
    if qty > available:
        raise ValueError(
            f"Insufficient stock for {sku}: requested {qty}, available {available}"
        )
    if qty < 0:
        raise ValueError(f"Cannot reserve negative quantity: {qty}")
    return available


def reserve(stock: dict, sku: str, qty: int) -> dict:
    """Reserve qty units of sku from stock and return updated stock.

    Raises:
        ValueError: When sku is unknown or qty exceeds available stock.
    """
    available = _validate_request(stock, sku, qty)
    return {**stock, sku: available - qty}
