"""Test OpenSupplyHub login: inspect response from /user-login/."""
import requests

BASE = "https://opensupplyhub.org"
s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Accept": "application/json",
    "Origin": BASE,
})
r = s.get(f"{BASE}/", timeout=15)
r = s.post(
    BASE + "/user-login/",
    json={"email": "test@test.com", "password": "wrong"},
    headers={"Referer": BASE + "/auth/login/"},
    timeout=15,
)
print("Status:", r.status_code)
print("Response headers Set-Cookie:", r.headers.get("Set-Cookie"))
print("Response body (first 500 chars):", r.text[:500] if r.text else "empty")
print("Cookies in session:", [(c.name, c.value[:30]) for c in s.cookies])
