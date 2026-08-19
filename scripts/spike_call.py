"""Phase 0 spike: one end-to-end call with a structured result.

Creates insights -> group -> assistant, dials YOUR OWN phone with dynamic
variables and string metadata, waits for the webhooks, fetches the insight
results and transcript, then cleans up — verifying along the way that
deleting the assistant does not cascade to its TeXML application
(FR-LAUNCH-6).

Run the webhook receiver first, on a public URL:

    uv run uvicorn becca.web.app:create_app --factory --port 8000
    cloudflared tunnel --url http://localhost:8000

Then:

    TELNYX_MODE=real uv run python scripts/spike_call.py \
        --to +2348030001188 --from +23418884120 \
        --webhook-base https://<tunnel>.trycloudflare.com

With TELNYX_MODE=fake (the default) the whole sequence runs against the
in-memory gateway as a rehearsal: no network, no call, no cost.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from becca.config import load_settings
from becca.telnyx.fake_gateway import FakeTelnyxGateway
from becca.telnyx.gateway import TelnyxGateway, TelnyxNotFound
from becca.telnyx.http_gateway import HttpTelnyxGateway

EVENTS_FILE = Path("spike_events.jsonl")

INSTRUCTIONS = """\
You are calling on behalf of Sylvastar Residences. Be brief and warm.
Confirm you are speaking with {{first_name}}. They are booked for a site
visit on {{visit_date}} at {{visit_time}}. Ask whether they are still
attending. If they are not, ask briefly why. Thank them and end the call.
You must identify yourself as an automated assistant calling for
Sylvastar Residences and state the purpose of the call at the start.
"""

GREETING = "Good afternoon, is this {{first_name}}?"

INSIGHTS: list[dict[str, Any]] = [
    {
        "name": "still_attending",
        "instructions": "Is the contact still attending their booked site visit?",
        "json_schema": {
            "type": "object",
            "properties": {"still_attending": {"type": "string", "enum": ["yes", "no", "unsure"]}},
            "required": ["still_attending"],
            # Live finding: Telnyx 400s without this (error 10015).
            "additionalProperties": False,
        },
    },
    {
        "name": "summary",
        "instructions": "Two sentences: what was agreed on this call.",
        "json_schema": None,
    },
]

VARIABLES = {
    "first_name": "Chidinma",
    "visit_date": "Thursday the seventh of August",
    "visit_time": "eleven thirty in the morning",
}

METADATA = {"becca_agent_id": "spike", "becca_run_id": "spike", "becca_contact_id": "spike"}


def wait_for_conversation_id(timeout_s: float) -> str | None:
    """Poll the webhook receiver's JSONL file for a conversation id."""
    deadline = time.time() + timeout_s
    seen = 0
    while time.time() < deadline:
        if EVENTS_FILE.exists():
            lines = EVENTS_FILE.read_text(encoding="utf-8").splitlines()
            for line in lines[seen:]:
                event = json.loads(line)
                body = event.get("body")
                if not isinstance(body, dict):
                    continue
                data = body.get("data", body)
                payload = data.get("payload", data) if isinstance(data, dict) else {}
                for key in ("conversation_id", "ConversationId"):
                    if isinstance(payload, dict) and payload.get(key):
                        return str(payload[key])
            seen = len(lines)
        time.sleep(2)
    return None


async def run(args: argparse.Namespace) -> None:
    settings = load_settings()
    gateway: TelnyxGateway
    if settings.telnyx_mode == "real":
        print("TELNYX_MODE=real — this places a REAL, BILLED call to", args.to)
        gateway = HttpTelnyxGateway(
            api_key=settings.telnyx_api_key,
            base_url=settings.telnyx_base_url,
            environment=settings.environment,
            dial_allowlist=settings.dial_allowlist_numbers(),
        )
    else:
        print("TELNYX_MODE=fake — rehearsal against the in-memory gateway")
        gateway = FakeTelnyxGateway()

    print("1. Creating insights…")
    insight_ids = [
        await gateway.create_insight(
            name=i["name"], instructions=i["instructions"], json_schema=i["json_schema"]
        )
        for i in INSIGHTS
    ]
    print("   ", insight_ids)

    print("2. Creating insight group and assigning…")
    group_id = await gateway.create_insight_group(name="spike-group")
    for insight_id in insight_ids:
        await gateway.assign_insight_to_group(group_id=group_id, insight_id=insight_id)
    print("   ", group_id)

    print("3. Creating assistant (recording pinned off, FR-AGENT-12)…")
    assistant = await gateway.create_assistant(
        name="spike-assistant",
        instructions=INSTRUCTIONS,
        greeting=GREETING,
        voice=args.voice,
        insight_group_id=group_id,
        dynamic_variables=VARIABLES,
    )
    print(f"    assistant={assistant.id} texml_app={assistant.default_texml_app_id}")

    print("4. Placing the call with the full AMD block (FR-DISPATCH-4)…")
    call = await gateway.place_call(
        connection_id=assistant.default_texml_app_id,
        assistant_id=assistant.id,
        to=args.to,
        from_=args.from_,
        variables=VARIABLES,
        metadata=METADATA,
        record=False,
        status_callback=f"{args.webhook_base}/webhooks/telnyx",
        amd_status_callback=f"{args.webhook_base}/webhooks/telnyx",
    )
    print("    response:", json.dumps(call, indent=2, default=str)[:2000])

    print("5. Waiting for webhooks (answer the phone, have the chat, hang up)…")
    conversation_id = call.get("conversation_id") or wait_for_conversation_id(
        timeout_s=0 if settings.telnyx_mode == "fake" else 300
    )
    if conversation_id is None:
        print("    No conversation id seen in", EVENTS_FILE)
        print("    Check the webhook receiver, then re-run the fetch steps by hand.")
    else:
        # Insights are scored as the conversation ends; give them a moment.
        if settings.telnyx_mode == "real":
            await asyncio.sleep(20)
        print("6. Fetching insight results…")
        for insight in await gateway.get_conversation_insights(
            conversation_id=str(conversation_id)
        ):
            print("   ", json.dumps(insight, default=str)[:500])
        print("7. Mirroring the transcript…")
        for message in await gateway.get_conversation_messages(
            conversation_id=str(conversation_id)
        ):
            print("   ", json.dumps(message, default=str)[:300])

    print("8. Cleanup — and verifying the delete does NOT cascade (FR-LAUNCH-6)…")
    await gateway.delete_assistant(assistant_id=assistant.id)
    try:
        await gateway.get_assistant(assistant_id=assistant.id)
        print("    UNEXPECTED: assistant still exists after delete")
    except TelnyxNotFound:
        print("    assistant 404s after delete — good")
    try:
        await gateway.delete_texml_application(texml_app_id=assistant.default_texml_app_id)
        print("    TeXML app survived the assistant delete and needed its own delete —")
        print("    non-cascade CONFIRMED (the sweeper must always do both)")
    except TelnyxNotFound:
        print("    TeXML app was already gone — non-cascade NOT reproduced; note this")

    if isinstance(gateway, HttpTelnyxGateway):
        await gateway.aclose()
    print("Done. Record findings per docs/ (Phase 0 task 5).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", required=True, help="YOUR OWN phone, E.164")
    parser.add_argument("--from", dest="from_", required=True, help="Nigerian caller ID, E.164")
    parser.add_argument("--voice", default="Telnyx.NaturalHD.astra", help="verbatim voice id")
    parser.add_argument("--webhook-base", default="http://localhost:8000")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
