"""Microsoft Entra ID sign-in and the dashboard's JWT session endpoints.

There are no local accounts or passwords: users sign in with their Microsoft
work account (configured tenant only), and the backend then issues its own
short-lived session token in an HttpOnly cookie.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from math import ceil
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from app.services import microsoft_sso
from app.services.auth_store import AuthStore
from app.services.microsoft_sso import SsoConfig, SsoError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])

ACCESS_TOKEN_COOKIE = "voice_agent_access_token"
ACCESS_TOKEN_TTL_SECONDS = 60 * 60
TOKEN_ISSUER = "payment-followup-agent"
TOKEN_AUDIENCE = "payment-followup-dashboard"
# Short-lived cookie holding one sign-in attempt (state, nonce, PKCE verifier).
SSO_STATE_COOKIE = "voice_agent_sso_state"
SSO_COOKIE_PATH = "/api/auth/microsoft"


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    if not value or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ValueError("Invalid base64url value.")
    decoded = base64.b64decode(
        value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
    )
    if _b64encode(decoded) != value:
        raise ValueError("Non-canonical base64url value.")
    return decoded


def _issue_access_token(user: dict[str, Any], signing_key: bytes) -> str:
    now = int(time.time())
    header = _b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode()
    )
    payload = _b64encode(
        json.dumps(
            {
                "iss": TOKEN_ISSUER,
                "aud": TOKEN_AUDIENCE,
                "sub": user["id"],
                "name": user["name"],
                "email": user["email"],
                "iat": now,
                "exp": now + ACCESS_TOKEN_TTL_SECONDS,
                "jti": secrets.token_urlsafe(16),
            },
            separators=(",", ":"),
        ).encode()
    )
    signed = f"{header}.{payload}".encode("ascii")
    signature = hmac.new(signing_key, signed, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64encode(signature)}"


def read_access_token(token: str | None, signing_key: bytes) -> dict[str, str] | None:
    if not token or len(token) > 4096:
        return None
    try:
        header_part, payload_part, signature_part = token.split(".")
        header = json.loads(_b64decode(header_part))
        payload = json.loads(_b64decode(payload_part))
        signature = _b64decode(signature_part)
        if (
            not isinstance(header, dict)
            or header.get("alg") != "HS256"
            or header.get("typ") != "JWT"
        ):
            return None
        expected = hmac.new(
            signing_key, f"{header_part}.{payload_part}".encode("ascii"), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(signature, expected):
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("iss") != TOKEN_ISSUER
            or type(payload.get("exp")) is not int
            or payload["exp"] <= int(time.time())
        ):
            return None
        user = {
            "id": payload.get("sub"),
            "name": payload.get("name"),
            "email": payload.get("email"),
            "token_id": payload.get("jti"),
        }
        issued_at = payload.get("iat")
        if (
            not all(isinstance(value, str) and value for value in user.values())
            or type(issued_at) is not int
            or issued_at > int(time.time()) + 30
            or payload["exp"] <= issued_at
            or payload["exp"] - issued_at > ACCESS_TOKEN_TTL_SECONDS
            or payload.get("aud") != TOKEN_AUDIENCE
        ):
            return None
        return {
            "id": str(user["id"]),
            "name": str(user["name"]),
            "email": str(user["email"]),
            "token_id": str(user["token_id"]),
            "expires_at": str(payload["exp"]),
        }
    except (
        ValueError,
        TypeError,
        UnicodeError,
        json.JSONDecodeError,
        binascii.Error,
    ):
        return None


def get_request_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    scheme, separator, credentials = authorization.partition(" ")
    if separator and scheme.casefold() == "bearer" and credentials.strip():
        return credentials.strip()
    return request.cookies.get(ACCESS_TOKEN_COOKIE)


async def user_from_request(request: Request) -> dict[str, str] | None:
    store: AuthStore = request.app.state.auth_store
    user = read_access_token(get_request_token(request), store.signing_key)
    if user is None:
        return None
    if await store.is_token_revoked(user["token_id"]):
        return None
    return user


def is_allowed_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return False
    settings = request.app.state.settings
    allowed_origins = {value.rstrip("/") for value in settings.allowed_origins}
    request_origin = f"{request.url.scheme}://{request.url.netloc}".rstrip("/")
    return origin.rstrip("/") == request_origin or origin.rstrip("/") in allowed_origins


def _validate_auth_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and not is_allowed_origin(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request origin is not allowed.",
        )


async def _enforce_rate_limit(
    request: Request,
    store: AuthStore,
    scope: str,
    identifier: str,
    limit: int,
    window_seconds: int,
) -> None:
    retry_after = await store.consume_rate_limit(
        scope, identifier, limit, window_seconds
    )
    if retry_after:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts. Try again later.",
            headers={"Retry-After": str(ceil(retry_after))},
        )


def _client_address(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host


def _get_auth_store(request: Request) -> AuthStore:
    if not request.app.state.settings.auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User authentication is disabled.",
        )
    return request.app.state.auth_store


def _set_auth_cookie(response: Response, request: Request, token: str) -> None:
    scheme = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(
        ",", maxsplit=1
    )[0]
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=token,
        max_age=ACCESS_TOKEN_TTL_SECONDS,
        httponly=True,
        secure=scheme.strip().casefold() == "https",
        samesite="lax",
        path="/",
    )


def _is_secure_request(request: Request) -> bool:
    scheme = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(
        ",", maxsplit=1
    )[0]
    return scheme.strip().casefold() == "https"


def _sso_config(request: Request) -> SsoConfig:
    settings = request.app.state.settings
    if not settings.sso_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Microsoft sign-in is not configured (set AZURE_TENANT_ID, "
                "AZURE_CLIENT_ID and AZURE_CLIENT_SECRET)."
            ),
        )
    return SsoConfig(
        tenant_id=settings.azure_tenant_id,
        client_id=settings.azure_client_id,
        client_secret=settings.azure_client_secret.get_secret_value(),
        redirect_uri=settings.azure_redirect_uri,
    )


def _app_url(request: Request, path: str) -> str:
    return request.app.state.settings.app_base_url.rstrip("/") + path


@router.get("/microsoft/login")
async def microsoft_login(request: Request) -> RedirectResponse:
    """Send the browser to Microsoft to sign in."""
    store = _get_auth_store(request)
    config = _sso_config(request)
    await _enforce_rate_limit(
        request, store, "sso-login", _client_address(request), 30, 600
    )
    attempt = microsoft_sso.start_sign_in(config, store.signing_key)
    response = RedirectResponse(attempt.url, status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=SSO_STATE_COOKIE,
        value=attempt.state_cookie,
        max_age=microsoft_sso.STATE_TTL_SECONDS,
        httponly=True,
        secure=_is_secure_request(request),
        # Lax: the cookie must come back on Microsoft's top-level redirect.
        samesite="lax",
        path=SSO_COOKIE_PATH,
    )
    return response


@router.get("/microsoft/callback")
async def microsoft_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    """Finish the Microsoft sign-in and start a dashboard session."""
    store = _get_auth_store(request)
    config = _sso_config(request)
    try:
        if error:
            raise SsoError(
                "cancelled" if error == "access_denied" else "failed",
                f"Microsoft returned {error}: {(error_description or '')[:300]}",
            )
        attempt = microsoft_sso.read_state_cookie(
            request.cookies.get(SSO_STATE_COOKIE), store.signing_key
        )
        if not state or not hmac.compare_digest(state, attempt["state"]):
            raise SsoError("expired", "The sign-in state does not match.")
        if not code:
            raise SsoError("failed", "Microsoft did not return an authorization code.")
        await _enforce_rate_limit(
            request, store, "sso-callback", _client_address(request), 30, 600
        )
        tokens = await microsoft_sso.exchange_code(config, code, attempt["verifier"])
        user = microsoft_sso.user_from_id_token(
            tokens.get("id_token"), config, attempt["nonce"]
        )
    except SsoError as exc:
        logger.warning("Microsoft sign-in failed (%s): %s", exc.code, exc)
        response = RedirectResponse(
            _app_url(request, f"/?error={exc.code}"),
            status_code=status.HTTP_302_FOUND,
        )
    else:
        logger.info("Microsoft sign-in succeeded for user %s", user["id"])
        response = RedirectResponse(
            _app_url(request, "/dashboard"), status_code=status.HTTP_302_FOUND
        )
        _set_auth_cookie(
            response, request, _issue_access_token(user, store.signing_key)
        )
    response.delete_cookie(
        key=SSO_STATE_COOKIE,
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
        path=SSO_COOKIE_PATH,
    )
    return response


@router.get("/session")
async def get_session(request: Request) -> dict[str, Any]:
    if not request.app.state.settings.auth_enabled:
        return {"enabled": False, "authenticated": True, "user": None}
    user = await user_from_request(request)
    session_user = (
        {key: user[key] for key in ("id", "name", "email")}
        if user is not None
        else None
    )
    return {
        "enabled": True,
        "authenticated": user is not None,
        "user": session_user,
    }


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    _validate_auth_origin(request)
    if request.app.state.settings.auth_enabled:
        store = _get_auth_store(request)
        user = await user_from_request(request)
        if user is not None:
            await store.revoke_token(user["token_id"], int(user["expires_at"]))
    response.delete_cookie(
        key=ACCESS_TOKEN_COOKIE,
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
        path="/",
    )
    return {"logged_out": True}
