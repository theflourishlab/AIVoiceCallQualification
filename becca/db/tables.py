"""SQLAlchemy models for migration-0001 scope (FRD §14).

Every tenant-scoped table carries client_account_id — including ones
where it is derivable — so RLS policies never need joins (SD-10).
queue_item and call are here from the start because the governor's
correctness rests on their shape (FR-DISPATCH-9), and both carry an
idempotency key (SD-27).

FRD §14 calls the user table `user`; it is `app_user` here because
`user` is reserved in Postgres (recorded deviation).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedColumn, mapped_column


def _uuid_pk() -> MappedColumn[uuid.UUID]:
    return mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))


_now = text("now()")


class Base(DeclarativeBase):
    type_annotation_map = {  # noqa: RUF012 — SQLAlchemy class-level config, not state
        uuid.UUID: UUID(as_uuid=True),
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
        str: Text,
    }


class BeccaStaff(Base):
    __tablename__ = "becca_staff"
    id: Mapped[uuid.UUID] = _uuid_pk()
    google_email: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now)


class ClientAccount(Base):
    __tablename__ = "client_account"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str]
    billing_entity: Mapped[str]
    margin_pct: Mapped[float] = mapped_column(Numeric(5, 2))
    channel_allocation: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    recording_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    telnyx_billing_group_id: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default=text("'onboarding'"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by_user_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now)


class AppUser(Base):
    __tablename__ = "app_user"
    id: Mapped[uuid.UUID] = _uuid_pk()
    client_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client_account.id"))
    google_email: Mapped[str] = mapped_column(unique=True)
    role: Mapped[str]  # owner | member (CHECK in migration)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now)


class Agent(Base):
    __tablename__ = "agent"
    id: Mapped[uuid.UUID] = _uuid_pk()
    client_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client_account.id"))
    name: Mapped[str]
    status: Mapped[str] = mapped_column(server_default=text("'draft'"))
    current_version_id: Mapped[uuid.UUID | None]
    telnyx_scratch_assistant_id: Mapped[str | None]
    telnyx_scratch_texml_app_id: Mapped[str | None]
    telnyx_run_assistant_id: Mapped[str | None]
    telnyx_run_texml_app_id: Mapped[str | None]
    telnyx_insight_group_id: Mapped[str | None]
    duplicated_from_agent_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now)


class AgentVersion(Base):
    __tablename__ = "agent_version"
    id: Mapped[uuid.UUID] = _uuid_pk()
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent.id"))
    client_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client_account.id"))
    n: Mapped[int] = mapped_column(Integer)
    # The one artifact. NO separate contract/schema columns — both are
    # views over fields[] (FR-AGENT-2); storing them would reintroduce
    # the drift this model removes.
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB)
    script_blocks: Mapped[dict[str, Any]] = mapped_column(JSONB)
    voice_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    telephony_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now)


class QueueItem(Base):
    __tablename__ = "queue_item"
    id: Mapped[uuid.UUID] = _uuid_pk()
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent.id"))
    client_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client_account.id"))
    contact_id: Mapped[uuid.UUID | None]  # FK added by migration 0003
    state: Mapped[str] = mapped_column(server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now)
    # A crash between "row locked" and "call placed" must not dial twice
    # (SD-27); the ambiguous-failure reconciler queries Telnyx by this key.
    idempotency_key: Mapped[str] = mapped_column(unique=True)


class Call(Base):
    __tablename__ = "call"
    id: Mapped[uuid.UUID] = _uuid_pk()
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent.id"))
    client_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("client_account.id"))
    contact_id: Mapped[uuid.UUID | None]
    # Spike finding (6 Aug 2026): the dial response carries only call_sid;
    # the session id arrives on the status callback and is the join to the
    # conversation's metadata.
    telnyx_call_session_id: Mapped[str | None] = mapped_column(unique=True)
    telnyx_call_control_id: Mapped[str | None]
    telnyx_conversation_id: Mapped[str | None]
    telnyx_recording_id: Mapped[str | None]  # the ID, never a URL (FR-REC-3)
    answered_by: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default=text("'dialling'"))
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    cost_actual: Mapped[float | None] = mapped_column(Numeric(10, 4))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(unique=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_type: Mapped[str]  # staff | user
    actor_id: Mapped[uuid.UUID]
    client_account_id: Mapped[uuid.UUID | None]
    action: Mapped[str]
    target: Mapped[str | None]
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now)


class WebhookEvent(Base):
    """The idempotency ledger (SD-26). Not in FRD §14; required by it.

    Telnyx redelivers; INSERT ... ON CONFLICT DO NOTHING on
    telnyx_event_id is the first DB action of every webhook, and zero
    rows inserted means already seen.
    """

    __tablename__ = "webhook_event"
    id: Mapped[uuid.UUID] = _uuid_pk()
    telnyx_event_id: Mapped[str] = mapped_column(unique=True)
    event_type: Mapped[str]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
