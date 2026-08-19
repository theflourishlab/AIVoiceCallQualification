from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration.

    Fails closed: an unrecognised TELNYX_MODE is a startup error, and the
    default is "fake", so no environment reaches Telnyx without saying so
    explicitly. On top of that sits SD-13's guardrail: outside production
    the gateway only dials numbers on DIAL_ALLOWLIST, which is empty by
    default.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telnyx_mode: Literal["fake", "real"] = "fake"
    telnyx_api_key: str = ""
    telnyx_public_key: str = ""
    telnyx_base_url: str = "https://api.telnyx.com/v2"

    # becca_app is deliberately not a superuser: superusers bypass RLS.
    database_url: str = "postgresql+asyncpg://becca_app:becca@localhost:5433/becca"
    database_pool: Literal["default", "null"] = "default"

    # Signs the session cookie (SD-24). The dev default is fine locally;
    # production must set its own or refuse to start.
    session_secret: str = "dev-only-secret"  # noqa: S105 — guarded below
    environment: Literal["dev", "staging", "production"] = "dev"

    # Host-header routing (SD-08): two hostnames, one deployment.
    # localtest.me resolves to 127.0.0.1 publicly, and unlike localhost
    # it is a legal cookie Domain in Chrome — the session must span both
    # hosts (staff sign in on the console, then browse the client plane
    # after Entering). Production sets becca.live.
    client_host: str = "app.localtest.me"
    console_host: str = "console.localtest.me"
    cookie_domain: str = "localtest.me"

    google_client_id: str = ""
    google_client_secret: str = ""
    # FR-AUTH-2: the first Becca staff are configuration, not a screen.
    becca_staff_emails: str = ""  # comma-separated Google emails

    anthropic_api_key: str = ""
    sentry_dsn: str = ""

    # What every assistant runs on, applied at create AND re-asserted on
    # every scratch sync — so an env change propagates to existing
    # scratch assistants on the next test call. Deployment-wide until
    # the voice screen (issue #1 strand 3) makes them per-agent.
    # Eligibility is empirical: 14 of the account's 25 listed models
    # attach to assistants (verified 13 Aug 2026); /ai/models is not the
    # eligibility list.
    assistant_conversation_model: str = "moonshotai/Kimi-K2.6"
    # Voice ids verbatim from GET /text-to-speech/voices (FR-AGENT-10).
    assistant_voice: str = "Telnyx.Ultra.81d6df1d-6175-4f24-9641-76d14b6d67cc"  # Rachael

    # FR-NF-1: the account-wide concurrency ceiling. Fixed at 10, not
    # requestable by clients; allocations are zero-sum against it
    # (FR-CONSOLE-3) and the governor enforces the per-client split.
    channel_ceiling: int = 10

    # Caller ID fallback for test calls and for runs launched before a
    # number was assigned in the console (Phase 6). Runs launched after
    # Phase 6 dial from the client's assigned number instead.
    # Must be a Nigerian number on the account (FR-NF-4).
    telnyx_from_number: str = ""
    # SD-13: the ONLY numbers a non-production environment may dial,
    # comma-separated E.164 (your own phones). Empty means dial nobody.
    # Production ignores it.
    dial_allowlist: str = ""
    # Public base URL for webhook callbacks (a tunnel in dev, the real
    # host in production). When set, dials carry StatusCallback URLs and
    # Telnyx reports call progress — including failure causes.
    public_webhook_base: str = ""
    # Shown verbatim on the client billing view (FR-BILL-6 "payment
    # details"). Empty = "your Becca contact will share account details".
    bank_transfer_details: str = ""

    # What one Nigerian national number rents for per month, live-verified
    # on the July 2026 Telnyx invoice (NG-NATIONAL-RATE0-MRC — spike
    # findings §11a). Never appears in detail records. Since the wallet
    # (14 Aug 2026) Becca ABSORBS this — clients pay per-minute only —
    # so it exists for legacy invoice readability and the console's
    # margin arithmetic, never for client billing.
    number_mrc_usd: float = 35.0

    # Wallet reserve sizing: one in-flight call holds rate x this many
    # minutes. 15 matches the stuck-dialling and stale-test-run timeouts,
    # so an attempt is always reconciled before it could exceed its hold —
    # the hold is provably sufficient, at the price of conservatism.
    max_call_minutes: int = 15

    # Soft estimate for projections only (launch-screen suggested spend
    # cap, test-screen expected price) — never for billing or holds. A
    # typical qualification call runs about 2 minutes (spike §11).
    estimated_call_minutes: float = 2.0

    @model_validator(mode="after")
    def _real_mode_needs_credentials(self) -> Self:
        if self.telnyx_mode == "real" and not self.telnyx_api_key:
            raise ValueError("TELNYX_MODE=real requires TELNYX_API_KEY")
        if self.environment == "production" and self.session_secret == "dev-only-secret":  # noqa: S105
            raise ValueError("production requires a real SESSION_SECRET")
        return self

    def staff_emails(self) -> frozenset[str]:
        return frozenset(e.strip().lower() for e in self.becca_staff_emails.split(",") if e.strip())

    def dial_allowlist_numbers(self) -> frozenset[str]:
        return frozenset(n.strip() for n in self.dial_allowlist.split(",") if n.strip())


def load_settings() -> Settings:
    return Settings()
