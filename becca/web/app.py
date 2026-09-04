"""The two planes: separate applications sharing a database (FR-ARCH-1).

One deployment, two hostnames (SD-08): Host routing sends
console.becca.live to the console app and app.becca.live to the client
app. Webhooks mount before Host routing so they answer on any hostname,
with no session and no CSRF. In dev, any unmatched host (localhost)
falls through to the client app.
"""

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from markupsafe import Markup, escape
from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Host, Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from becca import obs
from becca.config import Settings, load_settings
from becca.db.session import SessionFactory, make_engine
from becca.domain import scriptfmt
from becca.generation.fake import FakeGenerator
from becca.generation.generate import AnthropicGenerator, Generator
from becca.telnyx.fake_gateway import FakeTelnyxGateway
from becca.telnyx.gateway import TelnyxGateway
from becca.telnyx.http_gateway import HttpTelnyxGateway
from becca.web import auth, webhooks
from becca.web.client_plane import agents as client_agents
from becca.web.client_plane import billing as client_billing
from becca.web.client_plane import contacts as client_contacts
from becca.web.client_plane import launch as client_launch
from becca.web.client_plane import notifications as client_notifications
from becca.web.client_plane import overview as client_overview
from becca.web.client_plane import results as client_results
from becca.web.client_plane import testing as client_testing
from becca.web.client_plane import voice as client_voice
from becca.web.console_plane import billing as console_billing
from becca.web.console_plane import clients as console_clients
from becca.web.console_plane import notifications as console_notifications
from becca.web.console_plane import numbers as console_numbers
from becca.web.console_plane import staff as console_staff
from becca.web.sessions import SessionStore
from becca.worker.loop import run_forever

_HERE = Path(__file__).parent


def _script_bold(value: str, first: bool = False) -> Markup:
    """Display-only: bold the behavioural section labels, prototype-style,
    with the shared spacing rules (domain/scriptfmt) applied for agents
    generated before spacing was baked in at generation time.

    Escapes first; the inserted tags are the only markup. Serialisation
    is unaffected — the chip editor reads textContent, where <b> and
    newlines survive as plain text. `first` is loop.first from the
    template: only the script's true first block sheds its leading break
    — a label opening a LATER block keeps its blank line (the bug that
    ran Role→Opening together whenever a field chip split the blocks).
    """
    escaped = str(escape(value))
    escaped = scriptfmt.CAPS_LABEL.sub(r"\n\n<b>\1</b> ", escaped)

    def _bold_with_break(m: re.Match[str]) -> str:
        prefix = "\n\n" if m.group(1) else ""
        return f"{prefix}<b>{m.group(2)}</b>{m.group(3)}"

    escaped = scriptfmt.SECTION_LABEL.sub(_bold_with_break, escaped)
    escaped = re.sub(r"\n{3,}", "\n\n", escaped)
    if first:
        escaped = escaped.lstrip("\n")
    # S704 is acceptable here: input is escaped above; only our own <b> tags are added.
    return Markup(escaped)  # noqa: S704


def _make_generator(settings: Settings) -> Generator:
    if settings.anthropic_api_key:
        return AnthropicGenerator(settings.anthropic_api_key)
    return FakeGenerator()


def _make_gateway(settings: Settings) -> TelnyxGateway:
    if settings.telnyx_mode == "real":
        return HttpTelnyxGateway(
            api_key=settings.telnyx_api_key,
            base_url=settings.telnyx_base_url,
            environment=settings.environment,
            dial_allowlist=settings.dial_allowlist_numbers(),
        )
    return FakeTelnyxGateway()


def _plane_app(
    plane: str,
    settings: Settings,
    db: SessionFactory,
    store: SessionStore,
    templates: Jinja2Templates,
    generator: Generator,
    telnyx: TelnyxGateway,
) -> FastAPI:
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.state.plane = plane
    app.state.settings = settings
    app.state.db = db
    app.state.sessions = store
    app.state.templates = templates
    app.state.generator = generator
    app.state.telnyx = telnyx
    # Authlib stores the OAuth state parameter in Starlette's session.
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")
    app.include_router(auth.build_router(settings, plane))
    if plane == "console":
        app.include_router(console_clients.router)
        app.include_router(console_numbers.router)
        app.include_router(console_staff.router)
        app.include_router(console_billing.router)
        app.include_router(console_notifications.router)
    else:
        app.include_router(client_overview.router)
        app.include_router(client_agents.router)
        app.include_router(client_testing.router)
        app.include_router(client_voice.router)
        app.include_router(client_contacts.router)
        app.include_router(client_launch.router)
        app.include_router(client_results.router)
        app.include_router(client_billing.router)
        app.include_router(client_notifications.router)
    return app


async def _healthz(_: Request) -> Response:
    """Liveness probe for uptime pingers (UptimeRobot sends HEAD by
    default; FastAPI's ``@get`` routes answer HEAD with 405, and a
    plain Starlette Route adds HEAD to GET for free). Answers on any
    hostname, before auth, without touching the database."""
    return PlainTextResponse("ok")


def create_app() -> Starlette:
    obs.init()  # scrubber active before anything can throw (FR-NF-6A)
    settings = load_settings()
    db = SessionFactory(make_engine(settings.database_url))
    store = SessionStore(settings.session_secret)
    templates = Jinja2Templates(directory=_HERE / "templates")
    templates.env.filters["script_bold"] = _script_bold
    generator = _make_generator(settings)
    telnyx = _make_gateway(settings)

    webhook_app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    webhook_app.state.db = db  # lifecycle ingest opens worker sessions
    webhook_app.include_router(webhooks.router)

    console_app = _plane_app("console", settings, db, store, templates, generator, telnyx)
    client_app = _plane_app("client", settings, db, store, templates, generator, telnyx)

    # Lifespan on the ROOT app only — Host/Mount never run the mounted
    # sub-apps' lifespans. INLINE_WORKER runs the whole worker loop in
    # this process, for deployments without a worker service (Render
    # free tier). One uvicorn worker only: N processes would run N loops.
    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        task = None
        if settings.inline_worker:
            print("becca inline worker: starting in-process loop", flush=True)
            task = asyncio.create_task(run_forever(db, telnyx, settings))
        yield
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    return Starlette(
        routes=[
            Route("/healthz", _healthz, methods=["GET"]),
            Mount("/webhooks", app=webhook_app),
            Host(settings.console_host, app=console_app),
            Host(settings.client_host, app=client_app),
            # Dev fallback: plain localhost reaches the client plane.
            Mount("/", app=client_app),
        ],
        lifespan=lifespan,
    )
