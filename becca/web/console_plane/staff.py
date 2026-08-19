"""Console: Becca staff (FR-AUTH-3) and the audit feed (FR-CONSOLE-8).

Staff are a list of Google email addresses. No invitation is sent; the
person signs in and it works. Removal is immediate: identity is
re-derived from the database on every request (SD-24), so the removed
address is refused on its very next request.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from becca.services import audit
from becca.web.deps import StaffContext, check_csrf_form, require_staff

router = APIRouter()

AUDIT_FEED_LIMIT = 30


@router.get("/staff", response_class=HTMLResponse)
async def staff_screen(
    request: Request, staff: Annotated[StaffContext, Depends(require_staff)]
) -> HTMLResponse:
    settings = request.app.state.settings
    async with request.app.state.db.console_session() as s:
        rows = (
            await s.execute(
                text("SELECT id, google_email, created_at FROM becca_staff ORDER BY created_at")
            )
        ).all()
        feed = (
            await s.execute(
                text(
                    """
                    SELECT a.created_at, a.actor_type, a.action, a.target,
                           coalesce(s.google_email, u.google_email) AS actor_email,
                           c.name AS client_name
                      FROM audit_log a
                      LEFT JOIN becca_staff s ON a.actor_type = 'staff' AND s.id = a.actor_id
                      LEFT JOIN app_user u ON a.actor_type = 'user' AND u.id = a.actor_id
                      LEFT JOIN client_account c ON c.id = a.client_account_id
                     ORDER BY a.created_at DESC LIMIT :n
                    """
                ),
                {"n": AUDIT_FEED_LIMIT},
            )
        ).all()
    seeded = settings.staff_emails()
    result: HTMLResponse = request.app.state.templates.TemplateResponse(
        request,
        "console/staff.html",
        {
            "plane": "console",
            "staff": staff,
            "members": [
                {
                    "id": r[0],
                    "email": r[1],
                    "created_at": r[2],
                    "is_me": r[0] == staff.staff_id,
                    "seeded": r[1] in seeded,
                }
                for r in rows
            ],
            "feed": [
                {
                    "at": r[0],
                    "actor_type": r[1],
                    "action": r[2].replace("_", " "),
                    "target": r[3],
                    "actor": r[4],
                    "client": r[5],
                }
                for r in feed
            ],
            "error": request.query_params.get("error"),
        },
    )
    return result


@router.post("/staff", dependencies=[Depends(check_csrf_form)])
async def add_staff(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    email: Annotated[str, Form()],
) -> RedirectResponse:
    address = email.strip().lower()
    if "@" not in address:
        return RedirectResponse("/staff?error=email", status_code=303)
    async with request.app.state.db.console_session() as s:
        inserted = (
            await s.execute(
                text(
                    "INSERT INTO becca_staff (google_email) VALUES (:e)"
                    " ON CONFLICT (google_email) DO NOTHING RETURNING id"
                ),
                {"e": address},
            )
        ).scalar_one_or_none()
        if inserted is None:
            return RedirectResponse("/staff?error=exists", status_code=303)
        await audit.record(
            s,
            actor_type="staff",
            actor_id=staff.staff_id,
            action="added_staff",
            target=address,
        )
    return RedirectResponse("/staff", status_code=303)


@router.post("/staff/{member_id}/remove", dependencies=[Depends(check_csrf_form)])
async def remove_staff(
    request: Request,
    staff: Annotated[StaffContext, Depends(require_staff)],
    member_id: uuid.UUID,
) -> RedirectResponse:
    if member_id == staff.staff_id:
        # Locking yourself out of the console is never what was meant.
        return RedirectResponse("/staff?error=self", status_code=303)
    async with request.app.state.db.console_session() as s:
        email = (
            await s.execute(
                text("SELECT google_email FROM becca_staff WHERE id = :id"),
                {"id": str(member_id)},
            )
        ).scalar_one_or_none()
        if email is None:
            return RedirectResponse("/staff", status_code=303)
        if email in request.app.state.settings.staff_emails():
            # FR-AUTH-2 re-seeds this address from configuration on its
            # next sign-in, so deleting the row would silently undo
            # itself. Refuse, and say why, rather than pretend.
            return RedirectResponse("/staff?error=seeded", status_code=303)
        removed = (
            await s.execute(
                text("DELETE FROM becca_staff WHERE id = :id RETURNING google_email"),
                {"id": str(member_id)},
            )
        ).scalar_one_or_none()
        if removed is None:
            return RedirectResponse("/staff", status_code=303)
        await audit.record(
            s,
            actor_type="staff",
            actor_id=staff.staff_id,
            action="removed_staff",
            target=removed,
        )
    return RedirectResponse("/staff", status_code=303)
