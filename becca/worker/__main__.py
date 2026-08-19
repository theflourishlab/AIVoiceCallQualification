"""Worker entrypoint: `python -m becca.worker`.

obs.init() runs before anything that can throw (FR-NF-6A), mirroring
the web entrypoint.
"""

import asyncio

from becca import obs


def main() -> None:
    obs.init()
    from becca.config import load_settings
    from becca.db.session import SessionFactory, make_engine
    from becca.telnyx.fake_gateway import FakeTelnyxGateway
    from becca.telnyx.gateway import TelnyxGateway
    from becca.telnyx.http_gateway import HttpTelnyxGateway
    from becca.worker.loop import run_forever

    settings = load_settings()
    gateway: TelnyxGateway
    if settings.telnyx_mode == "real":
        gateway = HttpTelnyxGateway(
            api_key=settings.telnyx_api_key,
            base_url=settings.telnyx_base_url,
            environment=settings.environment,
            dial_allowlist=settings.dial_allowlist_numbers(),
        )
    else:
        gateway = FakeTelnyxGateway()
    db = SessionFactory(make_engine(settings.database_url))
    print(f"becca worker up — telnyx_mode={settings.telnyx_mode}", flush=True)
    asyncio.run(run_forever(db, gateway, settings))


if __name__ == "__main__":
    main()
