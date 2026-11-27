"""Email/password registration, login and JWT session endpoints."""

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
from pydantic import BaseModel, Field, SecretStr, field_validator

from app.services.auth_store import AuthStore, DuplicateEmailError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["authentication"])

ACCESS_TOKEN_COOKIE = "voice_agent_access_token"
ACCESS_TOKEN_TTL_SECONDS = 60 * 60
TOKEN_ISSUER = "payment-followup-agent"
TOKEN_AUDIENCE = "payment-followup-dashboard"
EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name is required.")
        return normalized

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("Enter a valid email address.")
        return normalized


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("Enter a valid email address.")
        return normalized


class UserResponse(BaseModel):
    id: str
    name: str
    email: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TOKEN_TTL_SECONDS
    user: UserResponse


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


def _create_token_response(
    user: dict[str, Any], store: AuthStore, request: Request, response: Response
) -> TokenResponse:
    token = _issue_access_token(user, store.signing_key)
    _set_auth_cookie(response, request, token)
    return TokenResponse(access_token=token, user=UserResponse(**user))


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    payload: RegisterRequest, request: Request, response: Response
) -> TokenResponse:
    _validate_auth_origin(request)
    store = _get_auth_store(request)
    await _enforce_rate_limit(
        request, store, "register", _client_address(request), 10, 3600
    )
    try:
        user = await store.register(
            payload.name, payload.email, payload.password.get_secret_value()
        )
    except DuplicateEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        ) from exc
    return _create_token_response(user, store, request, response)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, request: Request, response: Response
) -> TokenResponse:
    _validate_auth_origin(request)
    store = _get_auth_store(request)
    await _enforce_rate_limit(request, store, "login-email", payload.email, 15, 600)
    await _enforce_rate_limit(
        request, store, "login-address", _client_address(request), 60, 600
    )
    user = await store.authenticate(payload.email, payload.password.get_secret_value())
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _create_token_response(user, store, request, response)


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
