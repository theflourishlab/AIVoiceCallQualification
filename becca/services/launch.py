"""Launch: the pre-flight and the nine-step sequence (FR-LAUNCH-1..7).

Pre-flight runs in the browser AND again server side inside launch(),
so a stale browser cannot bypass it (FR-LAUNCH-2). The sequence builds
the run assistant from our stored config — the same serialisation path
the scratch assistant uses, so the test path is the production path —
and NEVER the clone endpoint (FR-LAUNCH-4: a clone shares the insight
group with the original). Scratch cleanup is not part of the sequence
(FR-LAUNCH-5): the housekeeping sweeper owns it, so a cleanup failure
can never abort a launch.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import time as dt_time
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from becca.domain.model import AgentVersionContent
from becca.domain.serialize import (
    insight_payloads,
    natural_greeting,
    render_assistant_instructions,
)
from becca.domain.views import variable_contract
from becca.services import contacts as contacts_service
from becca.services.voice_config import EffectiveVoiceConfig
from becca.telnyx.gateway import TelnyxGateway

# Duration estimates use a flat per-call figure; spend estimates are
# rate x minutes since the wallet (Settings.estimated_call_minutes is
# the money knob — this one is only about calling-time projections).
ESTIMATED_CALL_MINUTES = 2.5


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    ok: bool


@dataclass(frozen=True)
class Preflight:
    blocking: tuple[Check, ...]
    advisories: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.blocking)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(c for c in self.blocking if not c.ok)


class LaunchRefused(Exception):
    """Server-side pre-flight failed (FR-LAUNCH-2's second line of
    defence) or the launch sequence could not complete."""


async def preflight(
    session: AsyncSession,
    *,
    agent: dict[str, Any],
    list_entry: dict[str, Any] | None,
    client_account_id: uuid.UUID,
    telnyx_mode: str,
    from_number: str,
    max_call_minutes: int = 15,
    estimated_call_minutes: float = 2.0,
) -> Preflight:
    """FR-LAUNCH-2. Since Phase 6 the caller ID is the number assigned to
    this account in the console (FR-CONSOLE-4: channels AND a number must
    both be assigned before launch); `from_number` is only the config
    fallback that keeps pre-Phase-6 dev flows alive in fake mode. This
    runs in a client session — RLS shows the client its own numbers."""
    account = (
        await session.execute(
            text(
                "SELECT acknowledged_at, channel_allocation, wallet_balance_usd,"
                " rate_per_min_usd FROM client_account WHERE id = :cid"
            ),
            {"cid": str(client_account_id)},
        )
    ).first()
    acknowledged = account is not None and account[0] is not None
    allocation = int(account[1]) if account else 0
    balance = float(account[2]) if account else 0.0
    rate = float(account[3]) if account else 0.0

    assigned = (
        await session.execute(
            text(
                "SELECT phone_e164, country FROM phone_number"
                " WHERE client_account_id = :cid AND number_use = 'outbound'"
                " ORDER BY assigned_at DESC LIMIT 1"
            ),
            {"cid": str(client_account_id)},
        )
    ).first()

    tests = (
        await session.execute(
            text("SELECT count(*) FROM test_run WHERE agent_id = :aid"),
            {"aid": str(agent["id"])},
        )
    ).scalar_one()

    content: AgentVersionContent = agent["content"]
    contract = variable_contract(content)
    mapped_ok = False
    diallable = 0
    if list_entry is not None:
        mapping = list_entry["column_mapping"]
        mapped_ok = (
            not contacts_service.unmapped_required(contract, mapping)
            and contacts_service.phone_column(mapping) is not None
        )
        diallable = int(list_entry["diallable_count"])

    # FR-CONSOLE-4: a number must be assigned in the console. The config
    # from-number keeps fake-mode dev flows working until one is.
    number_ok = assigned is not None or (telnyx_mode == "fake" and bool(from_number))
    in_country = assigned is None or str(assigned[1]) == "NG"

    blocking = (
        Check("acknowledged", "Client account acknowledgement accepted", acknowledged),
        Check("tested", "Agent has been test called", int(tests) > 0),
        Check("list", "A contact list is attached", list_entry is not None),
        Check("mapped", "All required variables are mapped", mapped_ok),
        Check("diallable", "The list has diallable contacts", diallable > 0),
        Check("channels", "Channels are allocated to this account", allocation > 0),
        Check("number", "A number is assigned to this account", number_ok),
        Check("caller_id", "The assigned number is in-country (Nigeria)", in_country),
        Check(
            "not_run",
            "This agent has not already run (an agent runs once)",
            agent["status"] in ("draft", "tested"),
        ),
        # The prepaid inversion of FR-BILL-8 (wallet, 14 Aug 2026): a
        # launch that could not dial a single call is a confusing
        # non-event, so it is refused here instead of pausing instantly.
        Check(
            "wallet",
            "Wallet can cover at least one call",
            await _wallet_covers_one(
                session,
                client_account_id=client_account_id,
                balance=balance,
                rate=rate,
                max_call_minutes=max_call_minutes,
            ),
        ),
    )

    advisories: list[str] = []
    if diallable and rate:
        est_spend = diallable * rate * estimated_call_minutes
        if balance < est_spend:
            advisories.append(
                f"Calling this whole list will cost about ${est_spend:,.0f};"
                f" your wallet holds ${balance:,.2f}. The run will pause"
                " partway until you top up — nothing is lost when it does."
            )
    if diallable and allocation:
        total_minutes = diallable * ESTIMATED_CALL_MINUTES / allocation
        hours = total_minutes / 60
        length = f"about {hours / 24:.1f} days" if hours > 24 else f"about {hours:.1f} hours"
        advisories.append(
            f"You have {allocation} channel{'s' if allocation != 1 else ''}."
            f" At {diallable} contacts this takes {length} of calling time."
        )
    advisories.append(
        "Launching freezes what this agent extracts for the whole run."
        " To change it afterwards, duplicate the agent."
    )
    return Preflight(blocking=blocking, advisories=tuple(advisories))


async def _wallet_covers_one(
    session: AsyncSession,
    *,
    client_account_id: uuid.UUID,
    balance: float,
    rate: float,
    max_call_minutes: int,
) -> bool:
    from becca.services import wallet

    held = await wallet.reserved(
        session, client_account_id=client_account_id, max_call_minutes=max_call_minutes
    )
    hold = wallet.per_call_reserve(Decimal(str(rate)), max_call_minutes)
    return balance - float(held) - float(hold) >= 0


async def acknowledge(
    session: AsyncSession, *, client_account_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    """FR-CONSOLE-2A: recorded once, with the accepting owner and a
    timestamp; never shown again."""
    await session.execute(
        text(
            "UPDATE client_account SET acknowledged_at = now(), acknowledged_by_user_id = :uid"
            " WHERE id = :cid AND acknowledged_at IS NULL"
        ),
        {"uid": str(user_id), "cid": str(client_account_id)},
    )
    # The acknowledgement may be the last onboarding item (FR-CONSOLE-4).
    from becca.services import console as console_service

    await console_service.refresh_client_status(session, client_account_id=client_account_id)


@dataclass
class _Created:
    """Telnyx objects made so far, for compensation on failure."""

    insight_ids: list[str] = field(default_factory=list)
    group_id: str | None = None
    assistant_id: str | None = None
    texml_app_id: str | None = None


async def launch(
    session: AsyncSession,
    gateway: TelnyxGateway,
    *,
    agent: dict[str, Any],
    list_entry: dict[str, Any],
    client_account_id: uuid.UUID,
    telnyx_mode: str,
    from_number: str,
    voice_config: EffectiveVoiceConfig,
    window_start: str,
    window_end: str,
    days: list[int],
    excluded_bands: list[dict[str, str]],
    timezone: str,
    retry_attempts: int,
    retry_interval_secs: int,
    spend_cap: float,
    not_before: str | None,
) -> None:
    """FR-LAUNCH-3, all nine steps. DB writes ride the caller's
    transaction (rolled back wholesale on failure); Telnyx objects are
    compensated explicitly. Step numbering follows the FRD.
    """
    # Second line of defence — never trust the browser's pre-flight.
    result = await preflight(
        session,
        agent=agent,
        list_entry=list_entry,
        client_account_id=client_account_id,
        telnyx_mode=telnyx_mode,
        from_number=from_number,
    )
    if not result.ok:
        raise LaunchRefused("; ".join(c.label for c in result.failures))

    agent_id: uuid.UUID = agent["id"]
    content: AgentVersionContent = agent["content"]

    # Step 1 — mirror test transcripts and results. Already satisfied
    # structurally: Phase 2 stores every test's transcript and results
    # in test_run at fetch time; nothing lives only at Telnyx.

    created = _Created()
    insight_map: dict[str, str] = {}  # field_id -> insight_id, for result attribution
    try:
        # Steps 2-4: a fresh snapshot of the schema, a group owned by
        # this run alone, one insight per output field.
        created.group_id = await gateway.create_insight_group(name=f"run-{agent_id}")
        for payload in insight_payloads(content):
            insight_id = await gateway.create_insight(
                name=payload["name"],
                instructions=payload["instructions"],
                json_schema=payload["json_schema"],
            )
            created.insight_ids.append(insight_id)
            insight_map[str(payload["field_id"])] = insight_id
            await gateway.assign_insight_to_group(group_id=created.group_id, insight_id=insight_id)

        # Step 5: the run assistant, from our stored config — same
        # serialisation as the scratch assistant. recording_settings is
        # pinned false in the gateway (FR-AGENT-12); the per-call Record
        # flag decides at dial time (FR-REC-2). Optional inputs default
        # to their natural-reading fallback (FR-CONTACT-9).
        assistant = await gateway.create_assistant(
            name=f"run — {agent['name']}",
            model=voice_config.model,
            instructions=render_assistant_instructions(content),
            greeting=natural_greeting(content),
            voice=voice_config.voice,
            voice_speed=voice_config.voice_speed,
            transcription_model=voice_config.transcription_model,
            interruption=voice_config.interruption,
            time_limit_secs=voice_config.time_limit_secs,
            idle_timeout_secs=voice_config.idle_timeout_secs,
            insight_group_id=created.group_id,
            dynamic_variables={f.key: f.default for f in variable_contract(content)},
        )
        created.assistant_id = assistant.id
        created.texml_app_id = assistant.default_texml_app_id
    except Exception:
        await _compensate(gateway, created)
        raise

    # Step 6 — point the client's number at the new assistant's TeXML
    # app. Deliberately NOT done: dials go out via POST
    # /texml/ai_calls/{app_id} carrying the assistant id, so the number
    # needs no repointing (proven live through Phases 4-5). Repointing
    # matters only for inbound, which v1 excludes. The dispatcher now
    # reads the caller ID from the console's number inventory (Phase 6).

    # Step 7 — populate OUR dispatch queue (FR-DISPATCH-1). The
    # idempotency key is deterministic so an ambiguous dial can always
    # be reconciled to its queue row (SD-27).
    await session.execute(
        text(
            """
            INSERT INTO queue_item (agent_id, client_account_id, contact_id,
                                    state, next_attempt_at, idempotency_key)
            SELECT :aid, :cid, c.id, 'pending',
                   coalesce(cast(:not_before AS timestamptz), now()),
                   'run-' || :aid_text || '-' || c.id::text
              FROM contact c
             WHERE c.contact_list_id = :lid AND c.diallable
            """
        ),
        {
            "aid": str(agent_id),
            "aid_text": str(agent_id),
            "cid": str(client_account_id),
            "lid": str(list_entry["id"]),
            "not_before": not_before,
        },
    )

    # Step 8 — the scratch assistant is now an orphan. Its ids stay on
    # the agent row; the sweeper recognises a non-testing status and
    # deletes assistant + TeXML app (FR-LAUNCH-5/6).

    # Step 9 — schedule row + status.
    await session.execute(
        text(
            """
            INSERT INTO run_schedule (agent_id, client_account_id, contact_list_id,
                                      window_start, window_end, days, excluded_bands,
                                      timezone, retry_attempts, retry_interval_secs,
                                      spend_cap, not_before)
            VALUES (:aid, :cid, :lid, :ws, :we, :days, :bands, :tz,
                    :retries, :interval, :cap, cast(:not_before AS timestamptz))
            """
        ),
        {
            "aid": str(agent_id),
            "cid": str(client_account_id),
            "lid": str(list_entry["id"]),
            "ws": dt_time.fromisoformat(window_start),
            "we": dt_time.fromisoformat(window_end),
            "days": days,
            "bands": json.dumps(excluded_bands),
            "tz": timezone,
            "retries": retry_attempts,
            "interval": retry_interval_secs,
            "cap": spend_cap,
            "not_before": not_before,
        },
    )
    await session.execute(
        text(
            "UPDATE agent SET status = :status, telnyx_run_assistant_id = :assistant,"
            " telnyx_run_texml_app_id = :app, telnyx_insight_group_id = :group,"
            " telnyx_run_insight_map = :imap"
            " WHERE id = :aid"
        ),
        {
            "status": "scheduled" if not_before else "dialling",
            "assistant": created.assistant_id,
            "app": created.texml_app_id,
            "group": created.group_id,
            "imap": json.dumps(insight_map),
            "aid": str(agent_id),
        },
    )


async def _compensate(gateway: TelnyxGateway, created: _Created) -> None:
    """Undo Telnyx objects after a failed sequence. Deletion endpoints
    exist for the assistant and its TeXML app; insights and groups have
    none in our gateway yet, so those ids are left as recorded orphans
    for the sweeper era (spike findings §7 — harmless)."""
    try:
        if created.assistant_id:
            await gateway.delete_assistant(assistant_id=created.assistant_id)
        if created.texml_app_id:
            await gateway.delete_texml_application(texml_app_id=created.texml_app_id)
    except Exception:  # noqa: S110 — compensation is best-effort by design
        pass
