"""Request context resolution and the role gates (FR-AUTH-6, FR-ARCH-2/3).

Every request re-derives identity from the database, which is what makes
signed-cookie revocation immediate: a removed membership refuses the
next request (SD-24).
"""

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, Request
from sqlalchemy import text

from becca.db.session import SessionFactory
from becca.web.sessions import SessionData, SessionStore


@dataclass(frozen=True)
class StaffContext:
    staff_id: uuid.UUID
    email: str
    session: SessionData


@dataclass(frozen=True)
class ClientContext:
    """Someone operating inside a client account: a client user, or Becca
    staff Entering (FR-ARCH-2) — always distinguishable, for the banner
    and for audit attribution."""

    client_account_id: uuid.UUID
    account_name: str
    actor_type: str  # "user" | "staff"
    actor_id: uuid.UUID
    email: str
    role: str  # owner | member; staff act with owner-equivalent breadth
    entered: bool
    session: SessionData


def _store(request: Request) -> SessionStore:
    store: SessionStore = request.app.state.sessions
    return store


def _db(request: Request) -> SessionFactory:
    db: SessionFactory = request.app.state.db
    return db


def _redirect_to_signin() -> HTTPException:
    return HTTPException(status_code=303, headers={"Location": "/auth/signin"})


async def require_staff(request: Request) -> StaffContext:
    """Console plane gate. A client session gets 403, not a redirect —
    the console must not reveal anything, including its sign-in (FR-ARCH-3)."""
    session = _store(request).read(request)
    if session is None:
        raise _redirect_to_signin()
    if session.kind != "staff":
        raise HTTPException(status_code=403)
    async with _db(request).console_session() as s:
        row = (
            await s.execute(
                text("SELECT id, google_email FROM becca_staff WHERE id = :id"),
                {"id": session.user_id},
            )
        ).first()
    if row is None:
        raise HTTPException(status_code=403)
    return StaffContext(staff_id=row[0], email=row[1], session=session)


async def require_client_context(request: Request) -> ClientContext:
    """Client plane gate: a client user, or staff who have Entered."""
    session = _store(request).read(request)
    if session is None:
        raise _redirect_to_signin()
    db = _db(request)

    if session.kind == "user":
        async with db.console_session() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT u.id, u.google_email, u.role, u.client_account_id, c.name"
                        " FROM app_user u JOIN client_account c ON c.id = u.client_account_id"
                        " WHERE u.id = :id"
                    ),
                    {"id": session.user_id},
                )
            ).first()
        if row is None:
            raise _redirect_to_signin()
        return ClientContext(
            client_account_id=row[3],
            account_name=row[4],
            actor_type="user",
            actor_id=row[0],
            email=row[1],
            role=row[2],
            entered=False,
            session=session,
        )

    if session.kind == "staff" and session.entered_client_id:
        async with db.console_session() as s:
            staff = (
                await s.execute(
                    text("SELECT id, google_email FROM becca_staff WHERE id = :id"),
                    {"id": session.user_id},
                )
            ).first()
            account = (
                await s.execute(
                    text("SELECT id, name FROM client_account WHERE id = :cid"),
                    {"cid": session.entered_client_id},
                )
            ).first()
        if staff is None or account is None:
            raise _redirect_to_signin()
        return ClientContext(
            client_account_id=account[0],
            account_name=account[1],
            actor_type="staff",
            actor_id=staff[0],
            email=staff[1],
            role="owner",
            entered=True,
            session=session,
        )

    if session.kind == "staff":
        # Staff who have not Entered a client account have nothing on the
        # client plane — send them home to the console rather than
        # looping them through sign-in.
        settings = request.app.state.settings
        scheme = "http" if settings.environment == "dev" else "https"
        port = f":{request.url.port}" if request.url.port else ""
        raise HTTPException(
            status_code=303,
            headers={"Location": f"{scheme}://{settings.console_host}{port}/"},
        )

    raise _redirect_to_signin()


async def require_owner(request: Request) -> ClientContext:
    """FR-AUTH-7: launching is the only permission separating the roles."""
    ctx = await require_client_context(request)
    if ctx.role != "owner":
        raise HTTPException(status_code=403, detail="Owners only")
    return ctx


async def check_csrf_form(request: Request) -> None:
    from becca.web.sessions import check_csrf

    session = _store(request).read(request)
    form = await request.form()
    token = form.get("csrf_token")
    if not check_csrf(session, token if isinstance(token, str) else None):
        raise HTTPException(status_code=403, detail="CSRF check failed")
