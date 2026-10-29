from demo_shop.orders import promo_for

def test_vip_precedence_over_newsletter():
    assert promo_for({"vip": True, "subscribed": True}) == "vip-10"
