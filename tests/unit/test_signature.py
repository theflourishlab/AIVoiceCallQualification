import base64

import pytest
from nacl.signing import SigningKey

from becca.telnyx.signature import InvalidWebhookSignature, verify_webhook


def _sign(body: bytes, timestamp: str) -> tuple[str, str]:
    signing_key = SigningKey.generate()
    public_key = base64.b64encode(bytes(signing_key.verify_key)).decode()
    signature = base64.b64encode(
        signing_key.sign(timestamp.encode() + b"|" + body).signature
    ).decode()
    return public_key, signature


def test_valid_signature_passes() -> None:
    body = b'{"data": {"event_type": "call.conversation.ended"}}'
    public_key, signature = _sign(body, "1000")
    verify_webhook(
        public_key_b64=public_key,
        signature_b64=signature,
        timestamp="1000",
        raw_body=body,
        now=1000.0,
    )


def test_tampered_body_rejected() -> None:
    public_key, signature = _sign(b"original", "1000")
    with pytest.raises(InvalidWebhookSignature):
        verify_webhook(
            public_key_b64=public_key,
            signature_b64=signature,
            timestamp="1000",
            raw_body=b"tampered",
            now=1000.0,
        )


def test_stale_timestamp_rejected() -> None:
    body = b"{}"
    public_key, signature = _sign(body, "1000")
    with pytest.raises(InvalidWebhookSignature):
        verify_webhook(
            public_key_b64=public_key,
            signature_b64=signature,
            timestamp="1000",
            raw_body=body,
            now=10_000.0,
        )


def test_garbage_signature_rejected() -> None:
    with pytest.raises(InvalidWebhookSignature):
        verify_webhook(
            public_key_b64="not base64!!",
            signature_b64="also not",
            timestamp="1000",
            raw_body=b"{}",
            now=1000.0,
        )
