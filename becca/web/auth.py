"""Google OAuth authenticates; it never creates an account (FR-AUTH-1).

An authenticated email either matches a Becca staff row (or the seeded
staff list, FR-AUTH-2), or a client user row Becca created on request
(FR-AUTH-5) — or it is refused with a pointer to their Becca contact.
There is no sign-up, no invitation, no pending state.

In dev with no Google credentials configured, /auth/dev?email= runs the
same membership resolution without the OAuth dance. It refuses to exist
outside dev.
"""

from dataclasses import dataclass

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import text

from becca.config import Settings
from becca.db.session import SessionFactory
from becca.web.sessions import SessionData, SessionStore, new_csrf_token


@dataclass(frozen=True)
class Membership:
    user_id: str
    kind: str  # "staff" | "user"


async def resolve_membership(
    factory: SessionFactory, settings: Settings, email: str
) -> Membership | None:
    email = email.strip().lower()
    async with factory.console_session() as s:
        staff = (
            await s.execute(
                text("SELECT id FROM becca_staff WHERE google_email = :e"), {"e": email}
            )
        ).scalar_one_or_none()
        if staff is None and email in settings.staff_emails():
            # FR-AUTH-2: seeded as configuration; materialised on first
            # sign-in. No invitation is sent; signing in just works.
            staff = (
                await s.execute(
                    text("INSERT INTO becca_staff (google_email) VALUES (:e) RETURNING id"),
                    {"e": email},
                )
            ).scalar_one()
        if staff is not None:
            return Membership(user_id=str(staff), kind="staff")

        user = (
            await s.execute(text("SELECT id FROM app_user WHERE google_email = :e"), {"e": email})
        ).scalar_one_or_none()
        if user is not None:
            await s.execute(
                text("UPDATE app_user SET last_seen_at = now() WHERE id = :id"),
                {"id": str(user)},
            )
            return Membership(user_id=str(user), kind="user")
    return None


def _session_for(membership: Membership) -> SessionData:
    return SessionData(
        user_id=membership.user_id, kind=membership.kind, csrf_token=new_csrf_token()
    )


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/auth")
    oauth = OAuth()  # type: ignore[no-untyped-call]
    if settings.google_client_id:
        oauth.register(  # type: ignore[no-untyped-call]
            name="google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email"},
        )

    async def _sign_in(request: Request, email: str) -> Response:
        factory: SessionFactory = request.app.state.db
        store: SessionStore = request.app.state.sessions
        membership = await resolve_membership(factory, settings, email)
        if membership is None:
            # FR-AUTH-1: refusal, directing them to their Becca contact.
            # The email goes to the process log only (never Sentry) so a
            # refused sign-in is diagnosable: it is usually an address
            # mismatch with what the console has on file.
            print(f"auth refused: no membership for {email.strip().lower()!r}", flush=True)
            return RedirectResponse("/auth/refused", status_code=303)
        # Land each kind on its own plane, whichever host the OAuth round
        # trip used: staff on the console, client users on the client
        # plane. The session cookie spans both hosts (COOKIE_DOMAIN), so
        # the cross-host redirect stays signed in. Before this, the
        # relative "/" left a client user whose flow ended console-side
        # staring at FR-ARCH-3's bare 403 with a valid session.
        scheme = "http" if settings.environment == "dev" else "https"
        port = f":{request.url.port}" if request.url.port else ""
        host = settings.console_host if membership.kind == "staff" else settings.client_host
        response = RedirectResponse(f"{scheme}://{host}{port}/", status_code=303)
        store.write(response, _session_for(membership))
        return response

    @router.get("/login")
    async def login(request: Request) -> Response:
        if not settings.google_client_id:
            if settings.environment == "dev":
                return RedirectResponse("/auth/dev-form", status_code=303)
            return HTMLResponse("Google OAuth is not configured", status_code=500)
        # Not url_for("callback"): under Host routing it resolves via the
        # root router, whose FIRST Host entry (the console's) stamps its
        # hostname onto the URL, so the client plane sent Google to the
        # console's callback, where the state cookie does not exist, and
        # every client sign-in died with mismatching_state. The request's
        # own base URL keeps the round trip on one host.
        redirect_uri = str(request.base_url.replace(path="/auth/callback"))
        result: Response = await oauth.google.authorize_redirect(request, redirect_uri)
        return result

    @router.get("/callback")
    async def callback(request: Request) -> Response:
        try:
            token = await oauth.google.authorize_access_token(request)
        except OAuthError as exc:
            # error=access_denied here usually means the Google OAuth app
            # is still in Testing mode and this person is not a test user.
            print(f"auth refused: oauth error {exc.error!r}: {exc.description!r}", flush=True)
            return RedirectResponse("/auth/refused", status_code=303)
        email = str(token.get("userinfo", {}).get("email", ""))
        if not email:
            print("auth refused: token carried no email", flush=True)
            return RedirectResponse("/auth/refused", status_code=303)
        return await _sign_in(request, email)

    if settings.environment == "dev" and not settings.google_client_id:

        @router.get("/dev-form")
        async def dev_form(request: Request) -> HTMLResponse:
            return HTMLResponse(
                '<form method="get" action="/auth/dev" style="margin:4rem">'
                "<p>DEV sign-in (no Google credentials configured)</p>"
                '<input name="email" placeholder="email"/><button>Sign in</button></form>'
            )

        @router.get("/dev")
        async def dev_login(request: Request, email: str) -> Response:
            return await _sign_in(request, email)

    @router.get("/refused")
    async def refused(request: Request) -> HTMLResponse:
        templates = request.app.state.templates
        result: HTMLResponse = templates.TemplateResponse(
            request, "refusal.html", {"plane": request.app.state.plane}, status_code=403
        )
        return result

    @router.get("/logout")
    async def logout(request: Request) -> Response:
        store: SessionStore = request.app.state.sessions
        response = RedirectResponse("/auth/signin", status_code=303)
        store.clear(response)
        return response

    @router.get("/signin")
    async def signin(request: Request) -> HTMLResponse:
        templates = request.app.state.templates
        result: HTMLResponse = templates.TemplateResponse(
            request, "signin.html", {"plane": request.app.state.plane}
        )
        return result

    return router
