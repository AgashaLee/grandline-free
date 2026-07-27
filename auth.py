"""Whop login + membership gate.

Adapted from the tennis predictor's proven Whop OAuth flow. Active only when
the WHOP_* env vars are all set (on Railway). With no env vars -- e.g. running
locally -- ``WHOP_ENABLED`` is False and the dashboard stays single-user and
open, exactly as before.

The flow: /whop/login -> Whop consent -> /whop/callback -> we exchange the code,
read the user's id, check they have an active membership, and issue a session
cookie. Everything the logged-in user does is then scoped to their own data by
their Whop ``user_id`` (see dashboard.py).
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import threading
from datetime import datetime, timedelta

import requests

# --- config (from environment) -----------------------------------------
WHOP_CLIENT_ID = os.environ.get("WHOP_CLIENT_ID", "")
WHOP_CLIENT_SECRET = os.environ.get("WHOP_CLIENT_SECRET", "")
WHOP_API_KEY = os.environ.get("WHOP_API_KEY", "")
WHOP_PRODUCT_ID = os.environ.get("WHOP_PRODUCT_ID", "")
WHOP_REDIRECT_URI = os.environ.get("WHOP_REDIRECT_URI", "")
WHOP_PRODUCT_URL = os.environ.get("WHOP_PRODUCT_URL", "https://whop.com/")

#: Comma-separated Whop user_ids always allowed (comp access / testing).
WHOP_ALLOW_USERS = {s.strip() for s in os.environ.get("WHOP_ALLOW_USERS", "").split(",") if s.strip()}

#: Gate is on only when every piece is configured. Otherwise open/single-user.
WHOP_ENABLED = bool(
    WHOP_CLIENT_ID and WHOP_CLIENT_SECRET and WHOP_API_KEY
    and WHOP_PRODUCT_ID and WHOP_REDIRECT_URI
)

_AUTH_URL = "https://api.whop.com/oauth/authorize"
_TOKEN_URL = "https://api.whop.com/oauth/token"
_MEMBERS_URL = "https://api.whop.com/api/v5/memberships"

COOKIE_SESSION = "tcg_session"
COOKIE_STATE = "tcg_oauth_state"
COOKIE_VERIFIER = "tcg_oauth_verifier"

# In-memory sessions: sid -> {user_id, username, expires}. Cleared on restart
# (users just re-login). Fine for a single container.
_SESSIONS: dict[str, dict] = {}
_LOCK = threading.Lock()


# --- OAuth helpers ------------------------------------------------------
def _pkce_pair() -> tuple[str, str]:
    """Whop requires PKCE even for confidential apps."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def authorize_url(state: str, challenge: str, nonce: str) -> str:
    from urllib.parse import urlencode
    qs = urlencode({
        "response_type": "code",
        "client_id": WHOP_CLIENT_ID,
        "redirect_uri": WHOP_REDIRECT_URI,
        "scope": "openid member:basic:read",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{_AUTH_URL}?{qs}"


def exchange_code(code: str, verifier: str) -> dict:
    """Trade the auth code for tokens.

    Whop's token endpoint expects an ``application/x-www-form-urlencoded`` body.
    We try three client-authentication styles in order -- credentials in the
    form body (the OAuth2 standard), HTTP Basic auth, then a JSON body -- so we
    work regardless of how Whop expects confidential clients to authenticate.
    If every style fails we raise with the server's actual response text, so a
    misconfiguration is diagnosable instead of a bare 401.
    """
    base = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": WHOP_REDIRECT_URI,
        "code_verifier": verifier,
    }
    basic = base64.b64encode(f"{WHOP_CLIENT_ID}:{WHOP_CLIENT_SECRET}".encode()).decode()
    attempts = (
        # 1) client id + secret in the form body (standard OAuth2)
        dict(data={**base, "client_id": WHOP_CLIENT_ID, "client_secret": WHOP_CLIENT_SECRET},
             headers={"Accept": "application/json"}),
        # 2) HTTP Basic auth, credentials in the header
        dict(data=base,
             headers={"Accept": "application/json", "Authorization": f"Basic {basic}"}),
        # 3) JSON body (some older Whop deployments accepted this)
        dict(json={**base, "client_id": WHOP_CLIENT_ID, "client_secret": WHOP_CLIENT_SECRET},
             headers={"Accept": "application/json"}),
    )

    last = ""
    for kw in attempts:
        try:
            r = requests.post(_TOKEN_URL, timeout=15, **kw)
        except requests.RequestException as exc:
            last = str(exc)
            continue
        if r.ok:
            return r.json()
        last = f"{r.status_code}: {r.text[:300]}"
    raise RuntimeError(f"token exchange rejected by Whop ({last})")


def user_info(access_token: str) -> dict:
    """Fetch the user's identity. Tries a few endpoints (Whop's have shifted)."""
    for url in ("https://api.whop.com/oauth/userinfo",
                "https://api.whop.com/api/v5/users/me",
                "https://api.whop.com/api/v5/me"):
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            continue
    raise RuntimeError("Whop user-info call failed on all endpoints")


def has_active_membership(user_id: str, access_token: str | None = None) -> bool:
    """True if the user has an active membership for our product. Fail closed."""
    if user_id in WHOP_ALLOW_USERS:
        return True

    from urllib.parse import urlencode
    qs_user = urlencode({"user_id": user_id, "product_id": WHOP_PRODUCT_ID, "status": "active"})
    qs_me = urlencode({"product_id": WHOP_PRODUCT_ID, "status": "active"})
    tokens = ([("oauth", access_token)] if access_token else []) + \
             ([("apik", WHOP_API_KEY)] if WHOP_API_KEY else [])

    for _label, tok in tokens:
        for url in (f"{_MEMBERS_URL}?{qs_user}",
                    f"https://api.whop.com/api/v5/me/memberships?{qs_me}",
                    f"https://api.whop.com/api/v2/memberships?{qs_user}"):
            try:
                r = requests.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=15)
                r.raise_for_status()
                data = r.json()
                items = data.get("data") if isinstance(data, dict) else data
                return bool(items)
            except requests.RequestException:
                continue
    return False


def extract_identity(user: dict) -> tuple[str, str]:
    """Pull (user_id, username) from Whop's varying response shapes."""
    data = user.get("data") or {}
    user_id = user.get("sub") or user.get("id") or user.get("user_id") or data.get("id") or ""
    username = (user.get("name") or user.get("preferred_username") or user.get("username")
                or data.get("username") or user_id)
    return user_id, username


# --- sessions -----------------------------------------------------------
def new_session(user_id: str, username: str) -> str:
    sid = secrets.token_urlsafe(32)
    with _LOCK:
        _SESSIONS[sid] = {"user_id": user_id, "username": username,
                          "expires": datetime.now() + timedelta(hours=24)}
    return sid


def get_session(sid: str | None) -> dict | None:
    if not sid:
        return None
    with _LOCK:
        s = _SESSIONS.get(sid)
        if s and s["expires"] < datetime.now():
            del _SESSIONS[sid]
            return None
        return s


def drop_session(sid: str | None) -> None:
    if sid:
        with _LOCK:
            _SESSIONS.pop(sid, None)


def new_login_state() -> tuple[str, str, str, str]:
    """Return (state, nonce, verifier, authorize_url) to start a login."""
    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(16)
    verifier, challenge = _pkce_pair()
    return state, nonce, verifier, authorize_url(state, challenge, nonce)


# --- gate page ----------------------------------------------------------
_GATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>One Piece Card Tracker</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{{font-family:-apple-system,system-ui,'Segoe UI',sans-serif;margin:0;min-height:100vh;
  display:flex;align-items:center;justify-content:center;padding:2rem;
  background:linear-gradient(180deg,#bfe9fb,#fff6e6);color:#3a2a1e}}
 .card{{background:#fffaf0;border:2px solid #ecdcc2;padding:2.6rem 2.2rem;border-radius:18px;
  max-width:460px;text-align:center;width:100%;box-shadow:0 10px 30px rgba(20,80,120,.15)}}
 h1{{font-size:1.5rem;margin:0 0 .6rem;color:#c62828}}
 p{{color:#9c8a76;line-height:1.6;margin:0 0 1.5rem}}
 .btn{{display:inline-block;font-weight:700;padding:.85rem 1.5rem;border-radius:12px;
  text-decoration:none;margin:.3rem .25rem}}
 .btn.primary{{background:#1799d6;color:#fff}}
 .btn.alt{{background:#fff;color:#3a2a1e;border:2px solid #ecdcc2}}
</style></head><body><div class="card">
 <div style="font-size:2.4rem">👒🏴‍☠️</div>
 <h1>{title}</h1><p>{message}</p>
 <a class="btn primary" href="{primary_href}">{primary_label}</a>
 <a class="btn alt" href="{secondary_href}">{secondary_label}</a>
</div></body></html>"""


def gate_page(title: str, message: str, primary_label: str, primary_href: str,
              secondary_label: str, secondary_href: str) -> bytes:
    return _GATE.format(title=title, message=message, primary_label=primary_label,
                        primary_href=primary_href, secondary_label=secondary_label,
                        secondary_href=secondary_href).encode("utf-8")
