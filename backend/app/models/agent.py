import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import Timestamps, UUIDPrimaryKey


class Agent(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "agents"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    voice: Mapped[str | None] = mapped_column(String(100))
    language: Mapped[str | None] = mapped_column(String(50))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")


class Workflow(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "workflows"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    actions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
