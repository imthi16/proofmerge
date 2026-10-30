from demo_shop.orders import promo_for


def test_subscribed_customer_gets_news_promotion():
    assert promo_for({"subscribed": True}) == "news-5"
