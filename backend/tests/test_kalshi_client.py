import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi.client import KalshiClient
from kalshi.models import KalshiBalance, KalshiOrderRef


def _pem():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    return priv, pem


class _Resp:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.content = b"x"
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload, status_code=200, text=""):
        self.payload = payload
        self.status_code = status_code
        self.text = text
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "params": params, "json": json})
        return _Resp(self.payload, status_code=self.status_code, text=self.text)


def test_get_balance_signs_correct_path_and_maps_dto():
    priv, pem = _pem()
    sess = _FakeSession({"balance": 482014, "portfolio_value": 488314})
    c = KalshiClient(key_id="abc", private_key_pem=pem, environment="demo", session=sess)
    bal = c.get_balance()

    assert isinstance(bal, KalshiBalance)
    assert bal.cash_cents == 482014 and bal.portfolio_value_cents == 488314

    call = sess.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://external-api.demo.kalshi.co/trade-api/v2/portfolio/balance"
    h = call["headers"]
    assert h["KALSHI-ACCESS-KEY"] == "abc"
    # the signature must verify over "{ts}GET/trade-api/v2/portfolio/balance"
    ts = h["KALSHI-ACCESS-TIMESTAMP"]
    priv.public_key().verify(
        base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]),
        f"{ts}GET/trade-api/v2/portfolio/balance".encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_submit_order_builds_v2_body():
    _, pem = _pem()
    sess = _FakeSession({"order_id": "o123", "remaining_count": "40.00"})
    c = KalshiClient(key_id="abc", private_key_pem=pem, environment="demo", session=sess)
    ref = c.submit_order(market_ticker="KXEPL-LEEDS-YES", side="yes", action="buy",
                         contracts=40, limit_cents=52, client_order_id="cid-1")

    assert isinstance(ref, KalshiOrderRef)
    assert ref.broker_order_id == "o123"
    body = sess.calls[0]["json"]
    assert body["ticker"] == "KXEPL-LEEDS-YES"
    # V2: fixed-point dollar strings + book side; buy YES -> bid.
    assert body["count"] == "40.00" and body["price"] == "0.5200"
    assert body["side"] == "bid"
    assert body["time_in_force"] == "good_till_canceled"
    assert body["self_trade_prevention_type"] == "taker_at_cross"
    assert sess.calls[0]["url"].endswith("/trade-api/v2/portfolio/events/orders")
    # V2 requires client_order_id to be a UUID — a non-UUID logical id is mapped
    # deterministically (uuid5) so retries stay idempotent.
    import uuid
    cid1 = uuid.UUID(body["client_order_id"])   # raises if not a valid UUID
    sess2 = _FakeSession({"order_id": "o2"})
    KalshiClient(key_id="abc", private_key_pem=pem, environment="demo", session=sess2).submit_order(
        market_ticker="KXEPL-LEEDS-YES", side="yes", action="buy",
        contracts=40, limit_cents=52, client_order_id="cid-1")
    assert sess2.calls[0]["json"]["client_order_id"] == str(cid1)   # deterministic


def test_submit_order_sell_maps_to_ask():
    _, pem = _pem()
    sess = _FakeSession({"order_id": "o9"})
    c = KalshiClient(key_id="abc", private_key_pem=pem, environment="demo", session=sess)
    c.submit_order(market_ticker="T", side="yes", action="sell",
                   contracts=5, limit_cents=30, client_order_id="c")
    assert sess.calls[0]["json"]["side"] == "ask"


def test_http_error_surfaces_response_body():
    import pytest
    _, pem = _pem()
    sess = _FakeSession({}, status_code=400, text='{"error":{"message":"invalid count"}}')
    c = KalshiClient(key_id="abc", private_key_pem=pem, environment="demo", session=sess)
    with pytest.raises(RuntimeError, match="invalid count"):
        c.submit_order(market_ticker="T", side="yes", action="buy",
                       contracts=1, limit_cents=50, client_order_id="c")


def test_prod_environment_uses_prod_host():
    _, pem = _pem()
    sess = _FakeSession({"balance": 0})
    c = KalshiClient(key_id="abc", private_key_pem=pem, environment="live", session=sess)
    c.get_balance()
    assert sess.calls[0]["url"].startswith("https://api.elections.kalshi.com")


class _RoutingSession:
    """Returns resting orders on the list call, empty on deletes."""

    def __init__(self):
        self.deletes = []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        if method == "GET" and url.endswith("/portfolio/events/orders"):
            return _Resp({"orders": [{"order_id": "o1"}, {"order_id": "o2"}]})
        if method == "DELETE":
            self.deletes.append(url)
            return _Resp({})
        return _Resp({})


def test_cancel_all_open_orders_cancels_each():
    _, pem = _pem()
    sess = _RoutingSession()
    c = KalshiClient(key_id="abc", private_key_pem=pem, environment="demo", session=sess)
    n = c.cancel_all_open_orders()
    assert n == 2
    assert any(u.endswith("/portfolio/events/orders/o1") for u in sess.deletes)
    assert any(u.endswith("/portfolio/events/orders/o2") for u in sess.deletes)


def test_get_resting_orders_excludes_filled():
    # The ?status=resting param isn't always honored on V2 — a FILLED order can
    # come back in the list. It must NOT surface as pending (it's an open position),
    # even when it has no remaining_count field (so the old code fell back to the
    # original `count` and leaked it).
    _, pem = _pem()
    sess = _FakeSession({"orders": [
        {"ticker": "MKT-A", "side": "yes", "status": "filled", "count": 25},          # filled, no remaining -> drop
        {"ticker": "MKT-B", "side": "yes", "status": "resting", "remaining_count": 0, "count": 10},  # 0 left -> drop
        {"ticker": "MKT-C", "side": "yes", "status": "resting", "remaining_count": 7, "count": 10,
         "yes_price_dollars": "0.40"},                                                 # genuinely resting -> keep
    ]})
    c = KalshiClient(key_id="abc", private_key_pem=pem, environment="demo", session=sess)
    out = c.get_resting_orders()
    assert [o["market_ticker"] for o in out] == ["MKT-C"]
    assert out[0]["contracts"] == 7 and out[0]["price_cents"] == 40
