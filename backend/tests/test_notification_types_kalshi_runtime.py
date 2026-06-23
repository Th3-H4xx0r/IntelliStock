from notification_types import classify, default_routing, public_types, type_for_key


def test_kalshi_runtime_category_exists_and_routes_to_push():
    t = type_for_key("kalshi_runtime")
    assert t is not None and t["group"] == "Kalshi"
    assert t["discord"] is True and t["push"] is True
    routing = default_routing()
    assert routing["kalshi_runtime"]["discord"] is True


def test_classify_resolves_kalshi_runtime():
    assert classify(notif_key="kalshi_runtime") == "kalshi_runtime"
    assert classify(content="KALSHI RUNTIME [abc] crashed") == "kalshi_runtime"


def test_kalshi_runtime_in_public_types_for_settings_ui():
    keys = {t["key"] for t in public_types()}
    assert "kalshi_runtime" in keys
