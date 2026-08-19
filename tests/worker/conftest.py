"""Seeding for worker tests: a launched run, ready to dial."""

import json
import uuid
from dataclasses import dataclass, field
from datetime import time

from sqlalchemy import text

from becca.db.session import SessionFactory

CONTENT = {
    "fields": [
        {"id": 1, "key": "first_name", "kind": "input", "required": True, "type": "text"},
        {
            "id": 2,
            "key": "consultant_name",
            "kind": "input",
            "required": False,
            "type": "text",
            "default": "a consultant",
        },
        {"id": 3, "key": "outcome", "kind": "output", "required": True, "type": "text"},
    ],
    "script_blocks": [
        {"type": "text", "content": "Greet "},
        {"type": "field_ref", "field_id": 1},
        {"type": "text", "content": " on behalf of "},
        {"type": "field_ref", "field_id": 2},
        {"type": "text", "content": " and confirm the visit."},
    ],
}


@dataclass
class SeededRun:
    client_id: uuid.UUID
    agent_id: uuid.UUID
    contact_ids: list[uuid.UUID] = field(default_factory=list)


async def seed_run(
    db: SessionFactory,
    *,
    contacts: int = 3,
    allocation: int = 2,
    retry_attempts: int = 0,
    spend_cap: float = 1000.0,
    window: tuple[str, str] = ("00:00", "23:59"),
    days: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7),
    paused: bool = False,
    status: str = "dialling",
    contact_variables: dict[str, str] | None = None,
    rate_per_min: float = 0.30,
    balance: float = 1000.0,
) -> SeededRun:
    async with db.console_session() as s:
        # Wallet defaults are generous so non-money tests never trip the
        # reserve gate; balance > 0 seeds a credit row so cache == ledger.
        client_id = (
            await s.execute(
                text(
                    "INSERT INTO client_account (name, billing_entity, margin_pct,"
                    " channel_allocation, acknowledged_at, rate_per_min_usd,"
                    " wallet_balance_usd)"
                    " VALUES ('sylvastar', 'sylvastar', 50, :alloc, now(), :rate, :bal)"
                    " RETURNING id"
                ),
                {"alloc": allocation, "rate": rate_per_min, "bal": balance},
            )
        ).scalar_one()
        if balance:
            await s.execute(
                text(
                    "INSERT INTO wallet_ledger (client_account_id, entry_type,"
                    " amount_usd, note, created_by)"
                    " VALUES (:cid, 'credit', :bal, 'test seed',"
                    " '00000000-0000-0000-0000-000000000000')"
                ),
                {"cid": str(client_id), "bal": balance},
            )
        agent_id = (
            await s.execute(
                text(
                    "INSERT INTO agent (client_account_id, name, status,"
                    " telnyx_run_assistant_id, telnyx_run_texml_app_id)"
                    " VALUES (:cid, 'visit-qualifier', :status,"
                    " 'fake-run-assistant', 'fake-run-app') RETURNING id"
                ),
                {"cid": str(client_id), "status": status},
            )
        ).scalar_one()
        version_id = (
            await s.execute(
                text(
                    "INSERT INTO agent_version (agent_id, client_account_id, n, fields,"
                    " script_blocks) VALUES (:aid, :cid, 1, :f, :b) RETURNING id"
                ),
                {
                    "aid": str(agent_id),
                    "cid": str(client_id),
                    "f": json.dumps(CONTENT["fields"]),
                    "b": json.dumps(CONTENT["script_blocks"]),
                },
            )
        ).scalar_one()
        await s.execute(
            text("UPDATE agent SET current_version_id = :vid WHERE id = :aid"),
            {"vid": str(version_id), "aid": str(agent_id)},
        )
        list_id = (
            await s.execute(
                text(
                    "INSERT INTO contact_list (client_account_id, agent_id, filename,"
                    " row_count, diallable_count, source_file)"
                    " VALUES (:cid, :aid, 'contacts.csv', :n, :n, :blob) RETURNING id"
                ),
                {"cid": str(client_id), "aid": str(agent_id), "n": contacts, "blob": b"x"},
            )
        ).scalar_one()

        seeded = SeededRun(client_id=client_id, agent_id=agent_id)
        variables = contact_variables if contact_variables is not None else {"1": "Chidinma"}
        for i in range(contacts):
            contact_id = (
                await s.execute(
                    text(
                        "INSERT INTO contact (contact_list_id, client_account_id, row_index,"
                        " phone_raw, phone_e164, variables, dedupe_key, diallable)"
                        " VALUES (:lid, :cid, :i, :raw, :e164, :vars, :key, true) RETURNING id"
                    ),
                    {
                        "lid": str(list_id),
                        "cid": str(client_id),
                        "i": i + 1,
                        "raw": f"0803 000 11{i:02d}",
                        "e164": f"+23480300011{i:02d}",
                        "vars": json.dumps(variables),
                        "key": f"key-{i}",
                    },
                )
            ).scalar_one()
            seeded.contact_ids.append(contact_id)
            await s.execute(
                text(
                    "INSERT INTO queue_item (agent_id, client_account_id, contact_id,"
                    " idempotency_key) VALUES (:aid, :cid, :contact, :key)"
                ),
                {
                    "aid": str(agent_id),
                    "cid": str(client_id),
                    "contact": str(contact_id),
                    "key": f"run-{agent_id}-{contact_id}",
                },
            )
        await s.execute(
            text(
                "INSERT INTO run_schedule (agent_id, client_account_id, contact_list_id,"
                " window_start, window_end, days, timezone, retry_attempts,"
                " retry_interval_secs, spend_cap, paused)"
                " VALUES (:aid, :cid, :lid, :ws, :we, :days, 'Africa/Lagos',"
                " :retries, 60, :cap, :paused)"
            ),
            {
                "aid": str(agent_id),
                "cid": str(client_id),
                "lid": str(list_id),
                "ws": time.fromisoformat(window[0]),
                "we": time.fromisoformat(window[1]),
                "days": list(days),
                "retries": retry_attempts,
                "cap": spend_cap,
                "paused": paused,
            },
        )
    return seeded
