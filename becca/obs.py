"""Sentry, with the scrubber configured before the first event (SD-28).

In this system an unhandled exception's stack frames hold transcripts,
phone numbers and uploaded spreadsheets. Nothing personal may leave our
infrastructure through an error report (FR-NF-6A), so:

1. request bodies, cookies, query strings and headers are dropped wholesale
2. local variables are stripped from every stack frame — this is where a
   transcript sits when the insights handler throws
3. anything phone-shaped is masked everywhere, including breadcrumbs

Both entrypoints (web, worker) call init() before any code can throw.
Captured data cannot be recalled; that is why this runs first.
"""

import re
from typing import Any

import sentry_sdk
from sentry_sdk.types import Event, Hint

from becca.config import load_settings

_E164 = re.compile(r"\+?\d{10,15}")


def _mask(o: Any) -> Any:
    if isinstance(o, str):
        return _E164.sub("[phone]", o)
    if isinstance(o, dict):
        return {k: _mask(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_mask(v) for v in o]
    return o


def scrub(event: Event, hint: Hint) -> Event | None:
    if "request" in event:
        for key in ("data", "cookies", "query_string", "headers"):
            event["request"].pop(key, None)
    for exc in event.get("exception", {}).get("values", []):
        for frame in exc.get("stacktrace", {}).get("frames", []):
            frame.pop("vars", None)
    masked: Event = _mask(dict(event))
    return masked


def init() -> None:
    settings = load_settings()
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        before_send=scrub,
        send_default_pii=False,
        include_local_variables=False,  # belt to the scrubber's braces
    )


def page(message: str) -> None:
    """Becca staff must look at this now (FR-DISPATCH-11). Sentry is the
    paging channel; without a DSN (dev, tests) it still reaches the
    process log."""
    print(f"PAGE: {message}", flush=True)
    sentry_sdk.capture_message(message, level="fatal")
