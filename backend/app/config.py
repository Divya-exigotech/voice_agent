"""Application configuration, read from environment variables / a local .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.schemas.customer import PaymentStatus

# backend/app/config.py -> repository root is two levels above the `app` package.
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # The single JSON file that is the source of the customer/invoice queue.
    # Relative paths are resolved against the repository root.
    customers_json_path: Path = REPO_ROOT / "data" / "customers.json"

    # --- Existing Microsoft Foundry voice agent -------------------------------
    # Project endpoint, e.g. https://<resource>.services.ai.azure.com/api/projects/<project>
    azure_ai_project_endpoint: str | None = None
    # Name of the voice agent created in the Foundry project.
    azure_ai_agent_name: str | None = None
    # Max wait for the session to start / for each agent reply.
    azure_ai_agent_timeout_seconds: float = 45.0

    # --- Azure sign-in (Entra ID) -------------------------------------------------
    # default: DefaultAzureCredential (az login, managed identity, env vars)
    # browser: sign in once in a browser window; no Azure CLI needed
    # client_secret: service principal (tenant + client id + secret below)
    azure_auth_method: Literal["default", "browser", "client_secret"] = "default"
    # Optional for browser (pins the tenant); required for client_secret.
    azure_tenant_id: str | None = None
    azure_client_id: str | None = None
    azure_client_secret: SecretStr | None = None

    # Local email/password accounts for the dashboard.
    auth_enabled: bool = True
    auth_database_path: Path = Path.home() / ".voice-agent" / "users.sqlite3"
    auth_jwt_secret: SecretStr | None = None

    # --- Transcript storage (Azure Blob, M4) ------------------------------------
    # e.g. https://<account>.blob.core.windows.net. When unset, transcripts are
    # kept in memory (development only; lost on restart).
    azure_storage_account_url: str | None = None
    # Private container; created by `python -m scripts.setup_blob`.
    azure_storage_container: str = "call-transcripts"

    # Browser origins allowed to open the voice WebSocket (JSON list in env).
    allowed_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # --- Agent behaviour -----------------------------------------------------
    agent_organization_name: str = "the accounts team"
    # Used to resolve "today" for promised-payment-date validation.
    business_timezone: str = "Asia/Kolkata"

    # Seconds between a call that ended with a payment promise and the customer's
    # next call becoming ready in the queue (0 turns follow-up calls off).
    followup_delay_seconds: int = Field(default=180, ge=0)
    # A call that ends with a promised payment *date* is followed up on that date
    # (or, for a promise to pay today, the next business day) at this hour in
    # `business_timezone`. Weekends are skipped: calls are made Monday to Friday.
    followup_call_hour: int = Field(default=10, ge=0, le=23)
    # Seconds between checks for scheduled calls that have become due.
    followup_poll_seconds: int = Field(default=60, ge=1)

    # --- Existing PostgreSQL database ------------------------------------------
    # When set, the database replaces customers.json: the queue is read from
    # call_tasks/clients/invoices, and sessions, outcomes and transcript turns
    # are written to calls/call_events/call_actions/call_transcripts.
    # e.g. postgresql://user:password@host:5432/dbname?sslmode=require
    database_url: SecretStr | None = None
    # Every query is scoped to this organization (organizations.id). Optional:
    # when unset and the database holds exactly one organization, that one is used.
    database_organization_id: UUID | None = None
    # Schema holding the tables (search_path).
    database_schema: str = Field(
        default="public", pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"
    )
    # Optional agents.id to set on calls.agent_id.
    database_agent_id: UUID | None = None
    # invoices has no currency column: organizations.settings->>'currency'
    # is used when present, otherwise this.
    database_default_currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    # clients.language is nullable.
    database_default_language: str = Field(default="English", min_length=1)
    # invoices.status values that mean money is still owed, mapped to the
    # app's payment status. Invoices with any other status are not called about.
    database_invoice_status_map: dict[str, PaymentStatus] = {
        "pending": "unpaid",
        "overdue": "overdue",
        "partially_paid": "partially_paid",
    }
    # Status values written to call_tasks.status / calls.status. They must
    # match the values the application that owns the schema expects.
    database_task_status_queued: str = Field(
        default="queued", min_length=1, max_length=30
    )
    database_task_status_in_progress: str = Field(
        default="in_progress", min_length=1, max_length=30
    )
    database_task_status_completed: str = Field(
        default="completed", min_length=1, max_length=30
    )
    # calls: initiated (column default) while the session runs, then completed,
    # declined (customer asked to stop) or failed (technical failure).
    database_call_status_in_progress: str = Field(
        default="initiated", min_length=1, max_length=30
    )
    database_call_status_completed: str = Field(
        default="completed", min_length=1, max_length=30
    )
    database_call_status_declined: str = Field(
        default="declined", min_length=1, max_length=30
    )
    database_call_status_failed: str = Field(
        default="failed", min_length=1, max_length=30
    )

    @field_validator("customers_json_path")
    @classmethod
    def _resolve_relative(cls, value: Path) -> Path:
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()

    @field_validator("database_invoice_status_map")
    @classmethod
    def _non_empty_status_map(
        cls, value: dict[str, PaymentStatus]
    ) -> dict[str, PaymentStatus]:
        if not value:
            raise ValueError("at least one open invoice status is required")
        return value

    @model_validator(mode="after")
    def _client_secret_needs_ids(self) -> "Settings":
        if self.azure_auth_method == "client_secret" and not (
            self.azure_tenant_id and self.azure_client_id and self.azure_client_secret
        ):
            raise ValueError(
                "AZURE_AUTH_METHOD=client_secret requires AZURE_TENANT_ID, "
                "AZURE_CLIENT_ID and AZURE_CLIENT_SECRET"
            )
        return self

    @property
    def agent_configured(self) -> bool:
        return bool(self.azure_ai_project_endpoint and self.azure_ai_agent_name)

    @property
    def database_configured(self) -> bool:
        return self.database_url is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()
