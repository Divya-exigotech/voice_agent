import asyncio
import threading
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from app.services import azure_auth
from app.services.azure_auth import (
    AsyncCredentialAdapter,
    BrowserSignInCredential,
    build_async_credential,
    build_credential,
)
from tests.conftest import isolated_settings

SCOPE = "https://ai.azure.com/.default"


class FakeRecord:
    def __init__(
        self, tenant_id: str = "tenant-a", username: str = "me@example.com"
    ) -> None:
        self.tenant_id = tenant_id
        self.username = username

    def serialize(self) -> str:
        return f"{self.tenant_id}|{self.username}"

    @classmethod
    def deserialize(cls, data: str) -> "FakeRecord":
        tenant, user = data.split("|")
        return cls(tenant, user)


class FakeBrowserCredential:
    instances: ClassVar[list["FakeBrowserCredential"]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.authenticate_calls = 0
        self.token_calls = 0
        self.closed = False
        FakeBrowserCredential.instances.append(self)

    def authenticate(self, *, scopes: list[str]) -> FakeRecord:
        self.authenticate_calls += 1
        return FakeRecord()

    def get_token(self, *scopes: str, **_: Any) -> str:
        self.token_calls += 1
        return f"token:{','.join(scopes)}"

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_identity(monkeypatch: pytest.MonkeyPatch) -> type[FakeBrowserCredential]:
    import azure.identity

    FakeBrowserCredential.instances = []
    monkeypatch.setattr(
        azure.identity, "InteractiveBrowserCredential", FakeBrowserCredential
    )
    monkeypatch.setattr(azure.identity, "AuthenticationRecord", FakeRecord)
    return FakeBrowserCredential


def test_default_method_is_default() -> None:
    assert isolated_settings().azure_auth_method == "default"


def test_client_secret_requires_all_ids() -> None:
    with pytest.raises(ValidationError, match="AZURE_CLIENT_SECRET"):
        isolated_settings(azure_auth_method="client_secret", azure_tenant_id="t")


def test_client_secret_builds_service_principal_credentials() -> None:
    from azure.identity import ClientSecretCredential
    from azure.identity.aio import ClientSecretCredential as AsyncClientSecretCredential

    settings = isolated_settings(
        azure_auth_method="client_secret",
        azure_tenant_id="00000000-0000-0000-0000-000000000001",
        azure_client_id="00000000-0000-0000-0000-000000000002",
        azure_client_secret="s3cret",
    )
    assert isinstance(build_credential(settings), ClientSecretCredential)

    async def build() -> Any:
        credential = build_async_credential(settings)
        await credential.close()
        return credential

    assert isinstance(asyncio.run(build()), AsyncClientSecretCredential)


def test_browser_signs_in_once_and_saves_record(
    tmp_path: Path, fake_identity: Any
) -> None:
    record_path = tmp_path / "record.json"
    credential = BrowserSignInCredential(record_path=record_path)

    assert credential.get_token(SCOPE) == f"token:{SCOPE}"
    assert credential.get_token(SCOPE) == f"token:{SCOPE}"

    inner = fake_identity.instances[0]
    assert inner.authenticate_calls == 1
    assert "cache_persistence_options" in inner.kwargs
    assert record_path.read_text(encoding="utf-8") == "tenant-a|me@example.com"


def test_browser_reuses_saved_record_without_prompting(
    tmp_path: Path, fake_identity: Any
) -> None:
    record_path = tmp_path / "record.json"
    record_path.write_text("tenant-a|me@example.com", encoding="utf-8")

    credential = BrowserSignInCredential(record_path=record_path)
    credential.get_token(SCOPE)

    inner = fake_identity.instances[0]
    assert inner.authenticate_calls == 0
    assert inner.kwargs["authentication_record"].username == "me@example.com"
    assert inner.kwargs["tenant_id"] == "tenant-a"


def test_browser_ignores_record_from_another_tenant(
    tmp_path: Path, fake_identity: Any
) -> None:
    record_path = tmp_path / "record.json"
    record_path.write_text("tenant-a|me@example.com", encoding="utf-8")

    credential = BrowserSignInCredential(tenant_id="tenant-b", record_path=record_path)
    credential.get_token(SCOPE)

    inner = fake_identity.instances[0]
    assert "authentication_record" not in inner.kwargs
    assert inner.kwargs["tenant_id"] == "tenant-b"
    assert inner.authenticate_calls == 1


def test_browser_ignores_corrupt_record(tmp_path: Path, fake_identity: Any) -> None:
    record_path = tmp_path / "record.json"
    record_path.write_text("not a record", encoding="utf-8")

    BrowserSignInCredential(record_path=record_path).get_token(SCOPE)

    assert fake_identity.instances[0].authenticate_calls == 1


def test_browser_concurrent_requests_open_one_browser(
    tmp_path: Path, fake_identity: Any
) -> None:
    credential = BrowserSignInCredential(record_path=tmp_path / "record.json")
    threads = [
        threading.Thread(target=credential.get_token, args=(SCOPE,)) for _ in range(5)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert fake_identity.instances[0].authenticate_calls == 1
    assert fake_identity.instances[0].token_calls == 5


def test_build_browser_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_identity: Any
) -> None:
    monkeypatch.setattr(azure_auth, "AUTH_RECORD_PATH", tmp_path / "record.json")
    settings = isolated_settings(
        azure_auth_method="browser", azure_tenant_id="tenant-x"
    )

    assert isinstance(build_async_credential(settings), AsyncCredentialAdapter)
    assert isinstance(build_credential(settings), BrowserSignInCredential)
    assert all(c.kwargs["tenant_id"] == "tenant-x" for c in fake_identity.instances)


def test_async_adapter_runs_sync_credential_off_the_loop() -> None:
    loop_thread = threading.get_ident()
    seen: dict[str, Any] = {}

    class SyncCredential:
        closed = False

        def get_token(self, *scopes: str, **kwargs: Any) -> str:
            seen["thread"] = threading.get_ident()
            seen["args"] = (scopes, kwargs)
            return "tok"

        def close(self) -> None:
            SyncCredential.closed = True

    async def run() -> str:
        async with AsyncCredentialAdapter(SyncCredential()) as credential:
            return await credential.get_token(SCOPE, tenant_id="t")

    assert asyncio.run(run()) == "tok"
    assert seen["thread"] != loop_thread
    assert seen["args"] == ((SCOPE,), {"tenant_id": "t"})
    assert SyncCredential.closed
