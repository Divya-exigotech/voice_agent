import base64
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.api.routes.auth import (
    ACCESS_TOKEN_COOKIE,
    SSO_STATE_COOKIE,
    _issue_access_token,
    read_access_token,
)
from app.main import create_app
from app.services import microsoft_sso
from app.services.auth_store import AuthStore
from app.services.microsoft_sso import SsoConfig, SsoError, user_from_id_token
from tests.conftest import isolated_settings

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
REDIRECT_URI = "http://localhost:3000/api/auth/microsoft/callback"
APP_URL = "http://localhost:3000"
USER = {"id": "entra:oid-1", "name": "Example User", "email": "user@example.com"}

CONFIG = SsoConfig(
    tenant_id=TENANT,
    client_id=CLIENT_ID,
    client_secret="secret",
    redirect_uri=REDIRECT_URI,
)


def build_auth_client(
    customers_file: Path, auth_database: Path, **overrides: Any
) -> tuple[TestClient, AuthStore]:
    store = AuthStore(auth_database)
    values: dict[str, Any] = {
        "auth_enabled": True,
        "customers_json_path": customers_file,
        "azure_tenant_id": TENANT,
        "azure_client_id": CLIENT_ID,
        "azure_client_secret": "secret",
        "azure_redirect_uri": REDIRECT_URI,
        "app_base_url": APP_URL,
        **overrides,
    }
    app = create_app(
        settings=isolated_settings(**values), connector=None, auth_store=store
    )
    return TestClient(app), store


def id_token(**claims: Any) -> str:
    now = int(time.time())
    payload = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": CLIENT_ID,
        "tid": TENANT,
        "oid": "oid-1",
        "name": "Example User",
        "preferred_username": "User@Example.com",
        "iat": now,
        "exp": now + 3600,
        **claims,
    }
    part = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{part}.signature"


def start_sign_in(client: TestClient) -> dict[str, list[str]]:
    response = client.get("/api/auth/microsoft/login", follow_redirects=False)
    assert response.status_code == 302
    return parse_qs(urlparse(response.headers["location"]).query)


def fake_exchange(monkeypatch: pytest.MonkeyPatch, store: AuthStore, **claims: Any):
    calls: list[tuple[str, str]] = []

    async def exchange(config: SsoConfig, code: str, verifier: str) -> dict[str, Any]:
        calls.append((code, verifier))
        return {"id_token": id_token(**claims)}

    monkeypatch.setattr(microsoft_sso, "exchange_code", exchange)
    return calls


def nonce_of(client: TestClient, store: AuthStore) -> str:
    cookie = client.cookies.get(SSO_STATE_COOKIE, path="/api/auth/microsoft")
    return microsoft_sso.read_state_cookie(cookie, store.signing_key)["nonce"]


def test_password_login_and_registration_are_removed(
    customers_file: Path, tmp_path: Path
) -> None:
    client, _ = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        for path in ("/api/auth/register", "/api/auth/login"):
            response = client.post(
                path, json={"email": "user@example.com", "password": "x" * 12}
            )
            assert response.status_code in (401, 404, 405)


def test_microsoft_login_redirects_to_the_tenant_with_pkce(
    customers_file: Path, tmp_path: Path
) -> None:
    client, _ = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        response = client.get("/api/auth/microsoft/login", follow_redirects=False)

    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize"
    )
    query = parse_qs(location.query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["openid profile email"]
    assert query["state"][0] and query["nonce"][0] and query["code_challenge"][0]
    cookie = response.headers["set-cookie"].lower()
    assert SSO_STATE_COOKIE in cookie and "httponly" in cookie
    assert "path=/api/auth/microsoft" in cookie


def test_microsoft_callback_signs_the_user_in(
    customers_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        query = start_sign_in(client)
        calls = fake_exchange(monkeypatch, store, nonce=nonce_of(client, store))
        response = client.get(
            "/api/auth/microsoft/callback",
            params={"code": "auth-code", "state": query["state"][0]},
            follow_redirects=False,
        )

        assert response.status_code == 302
        assert response.headers["location"] == f"{APP_URL}/dashboard"
        assert calls and calls[0][0] == "auth-code"
        token = response.cookies.get(ACCESS_TOKEN_COOKIE)
        user = read_access_token(token, store.signing_key)
        assert user is not None
        assert {k: user[k] for k in ("id", "name", "email")} == USER
        session = client.get("/api/auth/session").json()
        assert session == {"enabled": True, "authenticated": True, "user": USER}
        assert client.get("/api/queue").status_code == 200


def test_microsoft_callback_rejects_a_state_mismatch(
    customers_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        start_sign_in(client)
        calls = fake_exchange(monkeypatch, store, nonce=nonce_of(client, store))
        response = client.get(
            "/api/auth/microsoft/callback",
            params={"code": "auth-code", "state": "forged"},
            follow_redirects=False,
        )

        assert response.headers["location"] == f"{APP_URL}/?error=expired"
        assert calls == []  # the code is never redeemed
        assert ACCESS_TOKEN_COOKIE not in response.cookies
        assert client.get("/api/auth/session").json()["authenticated"] is False


def test_microsoft_callback_without_a_sign_in_attempt_is_rejected(
    customers_file: Path, tmp_path: Path
) -> None:
    client, _ = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        response = client.get(
            "/api/auth/microsoft/callback",
            params={"code": "auth-code", "state": "anything"},
            follow_redirects=False,
        )
    assert response.headers["location"] == f"{APP_URL}/?error=expired"


def test_microsoft_callback_reports_a_cancelled_sign_in(
    customers_file: Path, tmp_path: Path
) -> None:
    client, _ = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        query = start_sign_in(client)
        response = client.get(
            "/api/auth/microsoft/callback",
            params={"error": "access_denied", "state": query["state"][0]},
            follow_redirects=False,
        )
    assert response.headers["location"] == f"{APP_URL}/?error=cancelled"


def test_microsoft_callback_rejects_a_token_with_the_wrong_nonce(
    customers_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        query = start_sign_in(client)
        fake_exchange(monkeypatch, store, nonce="replayed")
        response = client.get(
            "/api/auth/microsoft/callback",
            params={"code": "auth-code", "state": query["state"][0]},
            follow_redirects=False,
        )
    assert response.headers["location"] == f"{APP_URL}/?error=failed"
    assert ACCESS_TOKEN_COOKIE not in response.cookies


@pytest.mark.parametrize(
    "claims",
    [
        {"tid": "99999999-0000-0000-0000-000000000000"},  # another tenant
        {"aud": "someone-else"},
        {"iss": "https://login.microsoftonline.com/common/v2.0"},
        {"exp": int(time.time()) - 3600},
        {"nonce": "other"},
    ],
)
def test_id_token_claims_are_checked(claims: dict[str, Any]) -> None:
    token = id_token(**{"nonce": "n-1", **claims})
    with pytest.raises(SsoError):
        user_from_id_token(token, CONFIG, "n-1")


def test_id_token_user_falls_back_to_the_email_for_a_missing_name() -> None:
    token = id_token(nonce="n-1", name=None, email="Person@Example.com")
    assert user_from_id_token(token, CONFIG, "n-1") == {
        "id": "entra:oid-1",
        "name": "person@example.com",
        "email": "person@example.com",
    }


def test_microsoft_sign_in_needs_the_app_registration(
    customers_file: Path, tmp_path: Path
) -> None:
    client, _ = build_auth_client(
        customers_file,
        tmp_path / "auth.sqlite3",
        azure_tenant_id=None,
        azure_client_id=None,
        azure_client_secret=None,
    )
    with client:
        response = client.get("/api/auth/microsoft/login", follow_redirects=False)
    assert response.status_code == 503


def test_protected_apis_accept_cookie_or_bearer_token(
    customers_file: Path, tmp_path: Path
) -> None:
    client, store = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        assert client.get("/api/queue").status_code == 401
        token = _issue_access_token(USER, store.signing_key)

        bearer = client.get("/api/queue", headers={"Authorization": f"Bearer {token}"})
        assert bearer.status_code == 200
        client.cookies.set(ACCESS_TOKEN_COOKIE, token)
        assert client.get("/api/queue").status_code == 200


def test_logout_revokes_token_and_clears_session_cookie(
    customers_file: Path, tmp_path: Path
) -> None:
    client, store = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        token = _issue_access_token(USER, store.signing_key)
        client.cookies.set(ACCESS_TOKEN_COOKIE, token)
        assert client.get("/api/auth/session").json()["authenticated"] is True

        assert client.post("/api/auth/logout").json() == {"logged_out": True}
        assert client.get("/api/auth/session").json()["authenticated"] is False
        assert (
            client.get(
                "/api/queue", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 401
        )


def test_signing_key_survives_backend_restart(
    customers_file: Path, tmp_path: Path
) -> None:
    database_path = tmp_path / "auth.sqlite3"
    first_client, first_store = build_auth_client(customers_file, database_path)
    with first_client:
        token = _issue_access_token(USER, first_store.signing_key)

    restarted_client, restarted_store = build_auth_client(customers_file, database_path)
    with restarted_client:
        user = read_access_token(token, restarted_store.signing_key)
        assert user is not None
        assert user["email"] == "user@example.com"


def test_logout_rejects_untrusted_browser_origins(
    customers_file: Path, tmp_path: Path
) -> None:
    client, _ = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        response = client.post(
            "/api/auth/logout", headers={"Origin": "https://attacker.example"}
        )
        assert response.status_code == 403


def test_malformed_tokens_are_rejected() -> None:
    for token in ("not-a-jwt", "é.é.é", "a.b.c", "...", "a" * 4097):
        assert read_access_token(token, b"test-signing-key" * 2) is None


async def test_auth_rate_limits_are_shared_and_persistent(
    tmp_path: Path,
) -> None:
    store = AuthStore(tmp_path / "auth.sqlite3")
    await store.initialize()

    assert await store.consume_rate_limit("login", "user@example.com", 2, 60) == 0
    assert await store.consume_rate_limit("login", "user@example.com", 2, 60) == 0
    assert await store.consume_rate_limit("login", "user@example.com", 2, 60) > 0


async def test_auth_store_accepts_external_signing_key(tmp_path: Path) -> None:
    signing_key = b"external-signing-key" * 2
    store = AuthStore(tmp_path / "auth.sqlite3", signing_key=signing_key)
    await store.initialize()
    assert store.signing_key == signing_key


def test_access_log_redacts_the_sign_in_code() -> None:
    import logging

    from app.main import RedactSecretQueryFilter

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1",
            "GET",
            "/api/auth/microsoft/callback?code=SECRET&state=S1&session_state=S2",
            "1.1",
            302,
        ),
        None,
    )
    RedactSecretQueryFilter().filter(record)
    message = record.getMessage()
    assert "SECRET" not in message and "S1" not in message and "S2" not in message
    assert "code=[redacted]" in message
