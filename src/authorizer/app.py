import hmac
import os

def handler(event, context):
    headers = event.get("headers") or {}
    value = headers.get("authorization") or headers.get("Authorization") or ""
    expected = os.environ.get("AUTH_TOKEN", "")
    token = value[7:] if value.lower().startswith("bearer ") else ""
    ok = bool(expected) and hmac.compare_digest(token.encode(), expected.encode())
    return {"isAuthorized": ok}
