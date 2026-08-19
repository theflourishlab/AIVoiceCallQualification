"""The worker: one async loop, no scheduler (SD-22).

dispatch_tick every ~3 seconds; housekeeping every Nth iteration. A
tick that throws is logged and the loop continues — the worker's job is
to still be running at 4am, and every per-run hazard is already handled
inside the tick (pause flags, refusals, reconciliation)."""

import asyncio
from datetime import UTC, datetime

import sentry_sdk

from becca.config import Settings
from becca.db.session import SessionFactory
from becca.services.results import results_tick
from becca.telnyx.gateway import TelnyxGateway
from becca.worker.dispatch import dispatch_tick
from becca.worker.housekeeping import housekeeping_tick

TICK_SECONDS = 3.0
HOUSEKEEPING_EVERY = 20  # ~once a minute


async def run_forever(db: SessionFactory, gateway: TelnyxGateway, settings: Settings) -> None:
    iteration = 0
    while True:
        iteration += 1
        try:
            await dispatch_tick(db, gateway, settings, now_utc=datetime.now(UTC))
            await results_tick(db, gateway)  # poll-on-completed (FR-RESULT-1)
            if iteration % HOUSEKEEPING_EVERY == 0:
                await housekeeping_tick(db, gateway)
        except Exception as exc:  # keep running; Sentry hears about it
            sentry_sdk.capture_exception(exc)
            print(f"worker tick failed: {exc!r}", flush=True)
        await asyncio.sleep(TICK_SECONDS)
