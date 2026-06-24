import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi.signing import signed_message, sign_request, access_headers


def _keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    return priv, pem


def test_signed_message_strips_query():
    msg = signed_message("get", "/trade-api/v2/portfolio/balance?foo=1", 1700000000000)
    assert msg == "1700000000000GET/trade-api/v2/portfolio/balance"


def test_signature_verifies_with_public_key():
    priv, pem = _keypair()
    ts = 1700000000000
    path = "/trade-api/v2/portfolio/balance"
    sig_b64 = sign_request(pem, "GET", path, ts)
    # Verifying with the public key must succeed (raises on failure).
    priv.public_key().verify(
        base64.b64decode(sig_b64),
        f"{ts}GET{path}".encode("utf-8"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )


def test_access_headers_shape():
    _, pem = _keypair()
    h = access_headers(key_id="abc", method="POST", path="/trade-api/v2/portfolio/orders",
                       ts_ms=1700000000000, private_key_pem=pem)
    assert h["KALSHI-ACCESS-KEY"] == "abc"
    assert h["KALSHI-ACCESS-TIMESTAMP"] == "1700000000000"
    assert len(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"])) > 0
