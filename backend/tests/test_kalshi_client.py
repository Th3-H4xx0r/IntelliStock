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
    def __init__(self, payload):
        self._payload = payload
        self.content = b"x"

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request(self, method, url, headers=None, params=None, json=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "params": params, "json": json})
        return _Resp(self.payload)


def test_get_balance_signs_correct_path_and_maps_dto():
    priv, pem = _pem()
    sess = _FakeSession({"balance": 482014, "portfolio_value": 488314})
    c = KalshiClient(key_id="abc", private_key_pem=pem, environment="demo", session=sess)
    bal = c.get_balance()

    assert isinstance(bal, KalshiBalance)
    assert bal.cash_cents == 482014 and bal.portfolio_value_cents == 488314

    call = sess.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://demo-api.kalshi.co/trade-api/v2/portfolio/balance"
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


def test_submit_order_builds_limit_body():
    _, pem = _pem()
    sess = _FakeSession({"order": {"order_id": "o123", "status": "resting"}})
    c = KalshiClient(key_id="abc", private_key_pem=pem, environment="demo", session=sess)
    ref = c.submit_order(market_ticker="KXEPL-LEEDS-YES", side="yes", action="buy",
                         contracts=40, limit_cents=52, client_order_id="cid-1")

    assert isinstance(ref, KalshiOrderRef)
    assert ref.broker_order_id == "o123" and ref.status == "resting"
    body = sess.calls[0]["json"]
    assert body["ticker"] == "KXEPL-LEEDS-YES"
    assert body["count"] == 40 and body["yes_price"] == 52
    assert body["type"] == "limit" and body["action"] == "buy"
    assert sess.calls[0]["url"].endswith("/trade-api/v2/portfolio/orders")


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
        if method == "GET" and url.endswith("/portfolio/orders"):
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
    assert any(u.endswith("/portfolio/orders/o1") for u in sess.deletes)
    assert any(u.endswith("/portfolio/orders/o2") for u in sess.deletes)
