"""Microsoft Entra ID credentials for Foundry and Blob Storage.

AZURE_AUTH_METHOD picks how the backend signs in to Azure:

  * ``default``       - DefaultAzureCredential: environment variables, managed
                        identity, Azure CLI / azd / PowerShell sign-in.
  * ``browser``       - opens a browser window the first time a token is needed
                        and signs in as you. No Azure CLI or app registration is
                        required. The sign-in is cached (encrypted, per OS user),
                        so restarts and ``--reload`` do not ask again until it
                        expires.
  * ``client_secret`` - a service principal: AZURE_TENANT_ID, AZURE_CLIENT_ID and
                        AZURE_CLIENT_SECRET.

Whichever identity is used needs the "Azure AI User" role on the Foundry project
(and "Storage Blob Data Contributor" for Blob transcript storage).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

# Shared by every process of this app, so a sign-in survives restarts.
TOKEN_CACHE_NAME = "ai-calling-agent"
AUTH_RECORD_PATH = Path.home() / ".ai-calling-agent" / "azure-auth-record.json"


class BrowserSignInCredential:
    """InteractiveBrowserCredential that remembers the signed-in account.

    The first token request opens the browser; the resulting authentication
    record is saved so later processes reuse the encrypted token cache silently.
    A lock stops concurrent requests from opening several browser windows.
    """

    def __init__(
        self, *, tenant_id: str | None = None, record_path: Path | None = None
    ) -> None:
        from azure.identity import (
            InteractiveBrowserCredential,
            TokenCachePersistenceOptions,
        )

        self._record_path = record_path or AUTH_RECORD_PATH
        record = self._load_record(tenant_id)
        self._signed_in = record is not None
        options: dict[str, Any] = {
            "cache_persistence_options": TokenCachePersistenceOptions(
                name=TOKEN_CACHE_NAME
            ),
        }
        if record is not None:
            options["authentication_record"] = record
        tenant = tenant_id or (record.tenant_id if record is not None else None)
        if tenant:
            options["tenant_id"] = tenant
        self._credential = InteractiveBrowserCredential(**options)
        self._lock = threading.Lock()

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        with self._lock:
            if not self._signed_in:
                logger.warning("Opening a browser window to sign in to Azure ...")
                record = self._credential.authenticate(scopes=list(scopes))
                self._save_record(record)
                self._signed_in = True
                logger.info("Signed in to Azure as %s", record.username)
            return self._credential.get_token(*scopes, **kwargs)

    def close(self) -> None:
        self._credential.close()

    def _load_record(self, tenant_id: str | None) -> Any:
        from azure.identity import AuthenticationRecord

        try:
            record = AuthenticationRecord.deserialize(
                self._record_path.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001 - corrupt file: sign in again
            logger.warning(
                "Ignoring unreadable Azure sign-in record %s: %s",
                self._record_path,
                exc,
            )
            return None
        if tenant_id and record.tenant_id.lower() != tenant_id.lower():
            # A different tenant was configured since the last sign-in.
            return None
        return record

    def _save_record(self, record: Any) -> None:
        try:
            self._record_path.parent.mkdir(parents=True, exist_ok=True)
            self._record_path.write_text(record.serialize(), encoding="utf-8")
        except OSError as exc:  # still signed in for this process
            logger.warning(
                "Could not save the Azure sign-in record to %s: %s",
                self._record_path,
                exc,
            )


class AsyncCredentialAdapter:
    """Expose a sync TokenCredential as an AsyncTokenCredential.

    Token requests run in a worker thread, so a browser sign-in never blocks
    the event loop.
    """

    def __init__(self, credential: Any) -> None:
        self._credential = credential

    async def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self._credential.get_token, *scopes, **kwargs)

    async def close(self) -> None:
        await asyncio.to_thread(self._credential.close)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()


def build_credential(settings: Settings) -> Any:
    """Sync credential (Blob Storage)."""
    method = settings.azure_auth_method
    if method == "browser":
        return BrowserSignInCredential(tenant_id=settings.azure_tenant_id)
    if method == "client_secret":
        from azure.identity import ClientSecretCredential

        return ClientSecretCredential(**_client_secret_args(settings))
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def build_async_credential(settings: Settings) -> Any:
    """Async credential (Foundry voice agent). Call inside the running event loop."""
    method = settings.azure_auth_method
    if method == "browser":
        return AsyncCredentialAdapter(
            BrowserSignInCredential(tenant_id=settings.azure_tenant_id)
        )
    if method == "client_secret":
        from azure.identity.aio import ClientSecretCredential

        return ClientSecretCredential(**_client_secret_args(settings))
    from azure.identity.aio import DefaultAzureCredential

    return DefaultAzureCredential()


def _client_secret_args(settings: Settings) -> dict[str, str]:
    secret = settings.azure_client_secret
    return {
        "tenant_id": settings.azure_tenant_id or "",
        "client_id": settings.azure_client_id or "",
        "client_secret": secret.get_secret_value() if secret is not None else "",
    }
