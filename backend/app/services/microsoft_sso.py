"""Microsoft Entra ID sign-in (OpenID Connect authorization code flow with PKCE).

The backend is a confidential client: it sends the browser to the tenant's
authorize endpoint, exchanges the returned code for an ID token with the client
secret, checks the token's claims and then issues the dashboard's own session
token. Only accounts in the configured tenant can sign in.

The ID token comes straight from the token endpoint over TLS, authenticated
with the client secret, so its signature is not re-verified (OpenID Connect
Core 3.1.3.7); issuer, audience, tenant, nonce and lifetime are checked.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import aiohttp

AUTHORITY = "https://login.microsoftonline.com"
SCOPES = "openid profile email"
STATE_TTL_SECONDS = 10 * 60
TOKEN_TIMEOUT_SECONDS = 15
# Allowed clock difference between Entra ID and this server.
CLOCK_SKEW_SECONDS = 300


class SsoError(Exception):
    """Sign-in failed; `code` is a short reason shown to the user."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SsoConfig:
    tenant_id: str
    client_id: str
    client_secret: str
    redirect_uri: str

    @property
    def authorize_url(self) -> str:
        return f"{AUTHORITY}/{self.tenant_id}/oauth2/v2.0/authorize"

    @property
    def token_url(self) -> str:
        return f"{AUTHORITY}/{self.tenant_id}/oauth2/v2.0/token"

    @property
    def issuer(self) -> str:
        return f"{AUTHORITY}/{self.tenant_id}/v2.0"


@dataclass(frozen=True)
class SignInRequest:
    """One sign-in attempt: where to send the browser and what to remember."""

    url: str
    state_cookie: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def start_sign_in(config: SsoConfig, signing_key: bytes) -> SignInRequest:
    """Build the authorize URL and a signed cookie holding state, nonce and PKCE verifier."""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    query = urlencode(
        {
            "client_id": config.client_id,
            "response_type": "code",
            "redirect_uri": config.redirect_uri,
            "response_mode": "query",
            "scope": SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    payload = {
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "exp": int(time.time()) + STATE_TTL_SECONDS,
    }
    return SignInRequest(
        url=f"{config.authorize_url}?{query}",
        state_cookie=_sign(payload, signing_key),
    )


def _sign(payload: dict[str, Any], key: bytes) -> str:
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64url(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def read_state_cookie(cookie: str | None, key: bytes) -> dict[str, str]:
    """Return the sign-in attempt stored in the cookie, or raise SsoError."""
    if not cookie or len(cookie) > 2048 or "." not in cookie:
        raise SsoError("expired", "The sign-in attempt was not found or has expired.")
    body, _, signature = cookie.partition(".")
    expected = _b64url(hmac.new(key, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        raise SsoError("expired", "The sign-in attempt was not found or has expired.")
    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, binascii.Error) as exc:
        raise SsoError("expired", "The sign-in attempt is invalid.") from exc
    if not isinstance(payload, dict) or int(payload.get("exp", 0)) <= time.time():
        raise SsoError("expired", "The sign-in attempt has expired.")
    if not all(isinstance(payload.get(k), str) for k in ("state", "nonce", "verifier")):
        raise SsoError("expired", "The sign-in attempt is invalid.")
    return payload


async def exchange_code(config: SsoConfig, code: str, verifier: str) -> dict[str, Any]:
    """Redeem the authorization code at the token endpoint; returns the token response."""
    form = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.redirect_uri,
        "code_verifier": verifier,
        "scope": SCOPES,
    }
    timeout = aiohttp.ClientTimeout(total=TOKEN_TIMEOUT_SECONDS)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(config.token_url, data=form) as response,
        ):
            body = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        raise SsoError(
            "unavailable", f"Could not reach Microsoft sign-in: {exc}"
        ) from exc
    if not isinstance(body, dict):
        raise SsoError("failed", "Microsoft sign-in returned an unexpected response.")
    if "error" in body or response.status != 200:
        description = str(body.get("error_description") or body.get("error") or "")
        raise SsoError("failed", f"Microsoft sign-in failed: {description[:300]}")
    return body


def user_from_id_token(id_token: Any, config: SsoConfig, nonce: str) -> dict[str, str]:
    """Check the ID token's claims and return the dashboard user (id, name, email)."""
    if not isinstance(id_token, str) or id_token.count(".") != 2:
        raise SsoError("failed", "Microsoft sign-in did not return an ID token.")
    try:
        claims = json.loads(_b64url_decode(id_token.split(".")[1]))
    except (ValueError, binascii.Error) as exc:
        raise SsoError("failed", "The ID token could not be read.") from exc
    if not isinstance(claims, dict):
        raise SsoError("failed", "The ID token could not be read.")

    now = time.time()
    exp, iat = claims.get("exp"), claims.get("iat")
    if (
        claims.get("iss") != config.issuer
        or claims.get("aud") != config.client_id
        or str(claims.get("tid", "")).casefold() != config.tenant_id.casefold()
        or not isinstance(exp, int)
        or not isinstance(iat, int)
        or exp <= now - CLOCK_SKEW_SECONDS
        or iat > now + CLOCK_SKEW_SECONDS
    ):
        raise SsoError("failed", "The ID token is not valid for this application.")
    if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(
        claims["nonce"], nonce
    ):
        raise SsoError("failed", "The ID token does not match this sign-in attempt.")

    object_id = claims.get("oid") or claims.get("sub")
    email = claims.get("email") or claims.get("preferred_username")
    if not isinstance(object_id, str) or not object_id or not isinstance(email, str):
        raise SsoError("failed", "The Microsoft account has no usable identity.")
    email = email.strip().casefold()
    name = claims.get("name")
    name = name.strip() if isinstance(name, str) and name.strip() else email
    return {"id": f"entra:{object_id}", "name": name[:100], "email": email[:254]}
