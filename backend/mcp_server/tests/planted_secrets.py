"""Synthetic credentials the redaction tests plant, assembled when this module is imported.

Each value has the shape a secret scanner looks for (and results.py redacts), so none is
written out whole anywhere in the repository: GitHub push protection rejects a branch that
holds one, real or not. None of them is a real credential.

The log fixtures hold {{NAME}} placeholders, and fill() puts these values back before the
JSON is parsed, so redaction sees exactly the text the producers would log.
"""
import json
import re

AWS_ACCESS_KEY_ID = "AK" + "IAIOSFODNN7EXAMPLE"
AWS_SESSION_KEY_ID = "AS" + "IAIOSFODNN7EXAMPLE"
ANTHROPIC_KEY_PREFIX = "sk-" + "ant-api03"
ANTHROPIC_API_KEY = ANTHROPIC_KEY_PREFIX + "-Xk9mQ2vL7pR4tW8yB3nF6hJ1cD5gS0aZ-eU9iO2kM7qN4rT8wY3bV6xC1fH5jL0pA_s4dG7"
OPENAI_API_KEY = "sk-" + "proj-AbCdEfGhIjKlMnOpQrSt"
GITHUB_PAT = "gh" + "p_aB3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6"
GITHUB_OAUTH_TOKEN = "gh" + "o_abcdefghijklmnopqrstuvwxyz0123456789"
SLACK_BOT_TOKEN_PREFIX = "xox" + "b-1234567890"
SLACK_BOT_TOKEN = SLACK_BOT_TOKEN_PREFIX + "-0987654321-AbCdEfGhIjKlMnOp"
SLACK_USER_TOKEN = "xox" + "p-1234567890-abcdef"
GOOGLE_API_KEY = "AI" + "zaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"
DISCORD_WEBHOOK_URL = ("https://discord.com/api/web" + "hooks/1234567890123456789/"
                       "AbCdEf-GhIjKl_MnOpQrStUvWxYz0123456789abcdefGHIJ")
PEM_BEGIN_RSA = "-----BEGIN " + "RSA PRIVATE KEY-----"
PEM_BEGIN_PKCS8 = "-----BEGIN " + "PRIVATE KEY-----"
# header.payload.signature
JWT_HS256 = ".".join(("eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", "c2lnbmF0dXJlc2lnbmF0dXJl"))
JWT_HS256_TYP = ".".join(("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "eyJzdWIiOiJ1LTEyMyIsImlhdCI6MTc2MzUwMDAwMH0",
                          "Zm9vYmFyYmF6cXV4c2lnbmF0dXJlMTIz"))
JWT_EDDSA = ".".join(("eyJhbGciOiJFZERTQSJ9", "eyJzdWIiOiJ1LTEyMyJ9AAAAAA", "c2lnLXNpZy1zaWctc2lnLXNpZw"))

_VALUES = {name: value for name, value in globals().items() if name.isupper() and isinstance(value, str)}
_PLACEHOLDER = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def fill(text):
    """text with each {{NAME}} replaced by NAME's value, escaped for a JSON string; an unknown NAME raises KeyError."""
    return _PLACEHOLDER.sub(lambda match: json.dumps(_VALUES[match.group(1)])[1:-1], text)
