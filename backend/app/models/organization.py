import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import Timestamps, UUIDPrimaryKey


class Organization(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(50), nullable=False, server_default="free")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class Integration(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "integrations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    api_url: Mapped[str] = mapped_column(Text, nullable=False)
    # Non-secret config plus a reference to the secret (e.g. Key Vault); never raw credentials.
    auth_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    sync_status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="idle")


class SyncRun(UUIDPrimaryKey, Base):
    __tablename__ = "sync_runs"

    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="running")


class AuditLog(UUIDPrimaryKey, Base):
    __tablename__ = "audit_logs"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE")
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_audit_logs_org_timestamp", "organization_id", "timestamp"),)
