"""Order aggregation and promotion utilities for the demo shop.

Cancelled lines stay in an order at the end of the line list with zero
quantity.
"""

SHIPPING_FEES = {"standard": 5.0, "express": 12.0}


def order_total(items: list[dict], shipping: str = "standard") -> float:
    """Total price of an order's items plus the shipping fee."""
    total = 0.0
    for item in items:
        total += item["qty"] * item["price"]
    return total + SHIPPING_FEES[shipping]


def line_totals(items: list[dict]) -> list[float]:
    """Return the per-line totals for an order, in order."""
    totals = []
    for item in items:
        totals.append(item["qty"] * item["price"])
    return totals


def record_purchase(ledger: dict[str, float], customer: str, amount: float) -> float:
    """Record a purchase and return the customer's new balance.

    Raises:
        ValueError: When amount is negative or customer is unknown.
    """
    if customer not in ledger:
        raise ValueError(f"Unknown customer: {customer}")
    ledger[customer] = ledger[customer] + amount
    if amount < 0:
        raise ValueError(f"Negative purchase amount: {amount}")
    return ledger[customer]


def promo_for(customer: dict) -> str:
    """Return the promotion a customer qualifies for.

    VIP customers qualify for the VIP promotion, newsletter subscribers
    for the newsletter promotion.
    """
    if customer.get("vip"):
        return "vip-10"
    if customer.get("subscribed"):
        return "news-5"
    return "none"
