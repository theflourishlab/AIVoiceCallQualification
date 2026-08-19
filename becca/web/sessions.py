"""Signed-cookie sessions (SD-24) and CSRF (SD-25).

The cookie is signed, not encrypted: it carries a user id, kind and CSRF
token, and nothing else — no email, no role, no client account name.
Revocation is the membership lookup on every request: a removed user's
next request finds no membership and is refused.
"""

import secrets
from dataclasses import dataclass
from typing import Any

from itsdangerous import BadSignature, URLSafeSerializer
from starlette.requests import Request
from starlette.responses import Response

COOKIE = "becca_session"


@dataclass
class SessionData:
    user_id: str
    kind: str  # "staff" | "user"
    csrf_token: str
    entered_client_id: str | None = None  # staff Entering a client account


class SessionStore:
    def __init__(self, secret: str) -> None:
        self._serializer = URLSafeSerializer(secret, salt="becca-session")

    def read(self, request: Request) -> SessionData | None:
        raw = request.cookies.get(COOKIE)
        if not raw:
            return None
        try:
            data: dict[str, Any] = self._serializer.loads(raw)
        except BadSignature:
            return None
        try:
            return SessionData(**data)
        except TypeError:
            return None

    def write(self, response: Response, session: SessionData) -> None:
        value = self._serializer.dumps(session.__dict__)
        assert isinstance(value, str)
        # All three flags are non-negotiable (SD-24). Secure is omitted in
        # dev only, where the host is plain-http localhost.
        from becca.config import load_settings

        settings = load_settings()
        response.set_cookie(
            COOKIE,
            value,
            httponly=True,
            secure=settings.environment != "dev",
            samesite="lax",
            max_age=60 * 60 * 24 * 14,
            domain=settings.cookie_domain or None,
        )

    def clear(self, response: Response) -> None:
        from becca.config import load_settings

        response.delete_cookie(COOKIE, domain=load_settings().cookie_domain or None)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def check_csrf(session: SessionData | None, submitted: str | None) -> bool:
    return (
        session is not None
        and submitted is not None
        and secrets.compare_digest(session.csrf_token, submitted)
    )
