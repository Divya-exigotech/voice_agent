from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.api.routes.auth import ACCESS_TOKEN_COOKIE, read_access_token
from app.main import create_app
from app.services.auth_store import AuthStore
from tests.conftest import isolated_settings


def build_auth_client(
    customers_file: Path, auth_database: Path
) -> tuple[TestClient, AuthStore]:
    store = AuthStore(auth_database)
    app = create_app(
        settings=isolated_settings(
            auth_enabled=True,
            customers_json_path=customers_file,
        ),
        connector=None,
        auth_store=store,
    )
    return TestClient(app), store


def test_registration_persists_account_and_issues_access_token(
    customers_file: Path, tmp_path: Path
) -> None:
    client, store = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        response = client.post(
            "/api/auth/register",
            json={
                "name": "  Example User ",
                "email": "User@Example.com",
                "password": "a-very-long-password",
            },
        )

        assert response.status_code == 201
        body: dict[str, Any] = response.json()
        assert body["token_type"] == "bearer"
        assert body["user"]["name"] == "Example User"
        assert body["user"]["email"] == "user@example.com"
        user = read_access_token(body["access_token"], store.signing_key)
        assert user is not None
        assert {key: user[key] for key in ("id", "name", "email")} == body["user"]
        assert response.cookies.get(ACCESS_TOKEN_COOKIE) == body["access_token"]
        assert response.headers["set-cookie"].lower().find("httponly") >= 0
        assert client.get("/api/auth/session").json()["authenticated"] is True


def test_login_issues_token_and_rejects_invalid_password(
    customers_file: Path, tmp_path: Path
) -> None:
    client, _ = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        registration = client.post(
            "/api/auth/register",
            json={
                "name": "Example User",
                "email": "user@example.com",
                "password": "a-very-long-password",
            },
        )
        assert registration.status_code == 201

        client.cookies.clear()
        login = client.post(
            "/api/auth/login",
            json={"email": "USER@example.com", "password": "a-very-long-password"},
        )
        assert login.status_code == 200
        assert login.json()["access_token"]

        invalid = client.post(
            "/api/auth/login",
            json={"email": "user@example.com", "password": "wrong-password"},
        )
        assert invalid.status_code == 401
        assert invalid.json()["detail"] == "Invalid email or password."


def test_duplicate_registration_and_password_validation(
    customers_file: Path, tmp_path: Path
) -> None:
    client, _ = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        payload = {
            "name": "Example User",
            "email": "user@example.com",
            "password": "a-very-long-password",
        }
        assert client.post("/api/auth/register", json=payload).status_code == 201
        assert client.post("/api/auth/register", json=payload).status_code == 409
        payload["email"] = "another@example.com"
        payload["password"] = "short"
        assert client.post("/api/auth/register", json=payload).status_code == 422


def test_protected_apis_accept_cookie_or_bearer_token(
    customers_file: Path, tmp_path: Path
) -> None:
    client, _ = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        assert client.get("/api/queue").status_code == 401
        response = client.post(
            "/api/auth/register",
            json={
                "name": "Example User",
                "email": "user@example.com",
                "password": "a-very-long-password",
            },
        )
        token = response.json()["access_token"]
        client.cookies.clear()

        bearer = client.get("/api/queue", headers={"Authorization": f"Bearer {token}"})
        assert bearer.status_code == 200


def test_logout_revokes_token_and_clears_session_cookie(
    customers_file: Path, tmp_path: Path
) -> None:
    client, _ = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        response = client.post(
            "/api/auth/register",
            json={
                "name": "Example User",
                "email": "user@example.com",
                "password": "a-very-long-password",
            },
        )
        assert response.status_code == 201
        assert client.get("/api/auth/session").json()["authenticated"] is True

        token = response.json()["access_token"]
        assert client.post("/api/auth/logout").json() == {"logged_out": True}
        assert client.get("/api/auth/session").json()["authenticated"] is False
        assert (
            client.get(
                "/api/queue", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 401
        )


def test_accounts_and_signing_key_survive_backend_restart(
    customers_file: Path, tmp_path: Path
) -> None:
    database_path = tmp_path / "auth.sqlite3"
    first_client, _ = build_auth_client(customers_file, database_path)
    with first_client:
        registration = first_client.post(
            "/api/auth/register",
            json={
                "name": "Example User",
                "email": "user@example.com",
                "password": "a-very-long-password",
            },
        )
        original_token = registration.json()["access_token"]

    restarted_client, restarted_store = build_auth_client(customers_file, database_path)
    with restarted_client:
        user = read_access_token(original_token, restarted_store.signing_key)
        assert user is not None
        assert user["email"] == "user@example.com"
        login = restarted_client.post(
            "/api/auth/login",
            json={"email": "user@example.com", "password": "a-very-long-password"},
        )
        assert login.status_code == 200


def test_auth_rejects_untrusted_browser_origins(
    customers_file: Path, tmp_path: Path
) -> None:
    client, _ = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        response = client.post(
            "/api/auth/register",
            headers={"Origin": "https://attacker.example"},
            json={
                "name": "Example User",
                "email": "user@example.com",
                "password": "a-very-long-password",
            },
        )
        assert response.status_code == 403


def test_registration_accepts_same_origin_api_docs_request(
    customers_file: Path, tmp_path: Path
) -> None:
    client, _ = build_auth_client(customers_file, tmp_path / "auth.sqlite3")
    with client:
        response = client.post(
            "/api/auth/register",
            headers={"Origin": "http://testserver"},
            json={
                "name": "Example User",
                "email": "user@example.com",
                "password": "a-very-long-password",
            },
        )
        assert response.status_code == 201


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
