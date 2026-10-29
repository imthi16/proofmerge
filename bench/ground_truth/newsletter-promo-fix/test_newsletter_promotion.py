"""Ground-truth: newsletter subscribers must receive their promotion."""

from demo_shop.orders import promo_for


def test_newsletter_customer_gets_their_promotion():
    assert promo_for({"newsletter": True}) == "news-5"
