import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import Timestamps, UUIDPrimaryKey


class CallTask(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "call_tasks"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE")
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflows.id", ondelete="SET NULL")
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="queued"
    )

    __table_args__ = (
        Index(
            "ix_call_tasks_org_status_scheduled",
            "organization_id",
            "status",
            "scheduled_at",
        ),
    )


class Call(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "calls"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE")
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL")
    )
    provider_call_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="initiated"
    )
    duration: Mapped[int | None] = mapped_column(Integer)  # seconds

    __table_args__ = (
        Index("ix_calls_org_client", "organization_id", "client_id"),
        Index("ix_calls_org_created", "organization_id", "created_at"),
        # Idempotency: a duplicate provider webhook must not create a second call.
        Index(
            "uq_calls_provider_call_id",
            "provider_call_id",
            unique=True,
            postgresql_where=text("provider_call_id IS NOT NULL"),
        ),
    )


class CallTranscript(UUIDPrimaryKey, Base):
    __tablename__ = "call_transcripts"

    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), index=True
    )
    speaker: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # agent or customer
    text: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CallEvent(UUIDPrimaryKey, Base):
    """Append-only call event log."""

    __tablename__ = "call_events"

    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CallAction(UUIDPrimaryKey, Base):
    __tablename__ = "call_actions"

    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), index=True
    )
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    action_status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="pending"
    )
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
