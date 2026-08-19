"""Ed25519 webhook signature verification (FR-NF-5, SD-26).

Telnyx signs `{timestamp}|{raw_body}` with the account's Ed25519 key and
sends the signature in `telnyx-signature-ed25519` plus the timestamp in
`telnyx-timestamp`. Verification reads the raw body before any parsing.
"""

import base64
import binascii
import time

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

_MAX_SKEW_SECONDS = 300


class InvalidWebhookSignature(Exception):
    pass


def verify_webhook(
    *,
    public_key_b64: str,
    signature_b64: str,
    timestamp: str,
    raw_body: bytes,
    now: float | None = None,
) -> None:
    """Raise InvalidWebhookSignature unless the payload is authentic and fresh."""
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise InvalidWebhookSignature("timestamp is not an integer") from exc
    if abs((now if now is not None else time.time()) - ts) > _MAX_SKEW_SECONDS:
        raise InvalidWebhookSignature("timestamp outside the allowed skew")
    try:
        key = VerifyKey(base64.b64decode(public_key_b64))
        signature = base64.b64decode(signature_b64)
    except (binascii.Error, ValueError) as exc:
        raise InvalidWebhookSignature("malformed key or signature") from exc
    message = timestamp.encode() + b"|" + raw_body
    try:
        key.verify(message, signature)
    except BadSignatureError as exc:
        raise InvalidWebhookSignature("signature does not match") from exc
