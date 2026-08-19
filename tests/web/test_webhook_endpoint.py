import base64
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey
from starlette.applications import Starlette

from becca.web import webhooks as webhooks_module


@pytest.fixture
def events_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "spike_events.jsonl"
    monkeypatch.setattr(webhooks_module, "EVENTS_FILE", path)
    return path


def test_signed_json_event_recorded_as_verified(
    app: Starlette, events_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = SigningKey.generate()
    monkeypatch.setenv("TELNYX_PUBLIC_KEY", base64.b64encode(bytes(key.verify_key)).decode())
    client = TestClient(app)
    body = json.dumps({"data": {"event_type": "call.conversation.ended"}}).encode()
    timestamp = str(int(time.time()))
    signature = base64.b64encode(key.sign(timestamp.encode() + b"|" + body).signature).decode()

    response = client.post(
        "/webhooks/telnyx",
        content=body,
        headers={
            "content-type": "application/json",
            "telnyx-signature-ed25519": signature,
            "telnyx-timestamp": timestamp,
        },
    )
    assert response.status_code == 200
    record = json.loads(events_file.read_text().splitlines()[0])
    assert record["signature_verdict"] == "verified"


def test_form_encoded_callback_parsed(app: Starlette, events_file: Path) -> None:
    """Spike finding: TeXML callbacks arrive form-encoded (and signed on
    the real account)."""
    client = TestClient(app)
    response = client.post(
        "/webhooks/telnyx",
        content=b"CallSid=abc&CallStatus=completed&CallSessionId=sess-1",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200
    record = json.loads(events_file.read_text().splitlines()[0])
    assert record["body"]["CallSessionId"] == "sess-1"
