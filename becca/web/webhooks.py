"""Telnyx webhook receiver.

Spike finding (6 Aug 2026): TeXML status callbacks arrive form-encoded
and Ed25519-SIGNED, and the AI conversation events are not delivered to
the per-call callback URL at all — so one route and one verification
story covers everything we currently receive. Since Phase 4 the status
callbacks also drive the call/queue lifecycle (services.ingest); the
raw log stays as the observability trail. Phase 5 grows ingest into
the full results pipeline.
"""

import asyncio
import json
import time
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request, Response

from becca.config import load_settings
from becca.services import ingest
from becca.telnyx.signature import InvalidWebhookSignature, verify_webhook

EVENTS_FILE = Path("spike_events.jsonl")

router = APIRouter()


def _append(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


@router.post("/telnyx")
async def telnyx_webhook(request: Request) -> Response:
    settings = load_settings()
    raw = await request.body()
    signature = request.headers.get("telnyx-signature-ed25519", "")
    timestamp = request.headers.get("telnyx-timestamp", "")

    verdict = "unsigned"
    if signature and timestamp:
        if not settings.telnyx_public_key:
            verdict = "signed_but_no_key_configured"
        else:
            try:
                verify_webhook(
                    public_key_b64=settings.telnyx_public_key,
                    signature_b64=signature,
                    timestamp=timestamp,
                    raw_body=raw,
                )
                verdict = "verified"
            except InvalidWebhookSignature as exc:
                verdict = f"invalid: {exc}"

    content_type = request.headers.get("content-type", "")
    try:
        body = json.loads(raw)
    except ValueError:
        if "application/x-www-form-urlencoded" in content_type:
            body = dict(parse_qsl(raw.decode(errors="replace")))
        else:
            body = raw.decode(errors="replace")

    # The raw log is a dev/staging observability trail; in production it
    # would grow without bound on (ephemeral) disk, relative to CWD.
    if settings.environment != "production":
        record = {
            "received_at": time.time(),
            "content_type": content_type,
            "signature_verdict": verdict,
            "body": body,
        }
        await asyncio.to_thread(_append, EVENTS_FILE, json.dumps(record) + "\n")

    # Lifecycle ingest (Phase 4). A configured public key makes
    # verification mandatory; without one (dev, tests) events are
    # processed as-is. The worker plane spans tenants by design.
    trusted = verdict == "verified" or not settings.telnyx_public_key
    db = getattr(request.app.state, "db", None)
    if trusted and db is not None and isinstance(body, dict) and body.get("CallSid"):
        async with db.worker_session() as s:
            await ingest.ingest_texml_callback(s, body, max_call_minutes=settings.max_call_minutes)
    return Response(status_code=200)
