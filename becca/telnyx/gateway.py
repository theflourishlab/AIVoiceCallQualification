"""The seam between Becca and Telnyx (SD-15).

Everything that talks to Telnyx goes through a TelnyxGateway. The HTTP
implementation carries the product rules (AMD on every call, per-call
Record flag, string metadata); the fake implementation is the dev/test
story. Nothing outside this package imports httpx to reach Telnyx.
"""

from dataclasses import dataclass
from typing import Any, Protocol


class TelnyxError(Exception):
    """A Telnyx request failed and was not retried."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Telnyx returned {status_code}: {body[:500]}")


class TelnyxNotFound(TelnyxError):
    """404 — used by the spike to verify non-cascading deletes (FR-LAUNCH-6)."""


class AmbiguousDialError(Exception):
    """A dial request may or may not have reached Telnyx (SD-16).

    The connection dropped after the request was sent, so the call may have
    been placed. Never retried blindly: retrying rings a real person twice.
    Resolution is reconciliation against Telnyx, not a retry.
    """


class DialRefused(Exception):
    """The SD-13 guardrail: outside production, only allowlisted numbers.

    One Telnyx account serves every environment — same key, same numbers,
    same balance — so a dev or staging dispatcher can ring real Nigerian
    mobiles. The refusal lives here in the gateway, not in config someone
    can forget, and is raised BEFORE any request is sent: nothing to
    reconcile, nothing billed.
    """

    def __init__(self, to: str, environment: str) -> None:
        self.to = to
        self.environment = environment
        super().__init__(
            f"refusing to dial {to}: environment {environment!r} is not production"
            " and the number is not on DIAL_ALLOWLIST (SD-13)"
        )


@dataclass(frozen=True)
class Assistant:
    id: str
    default_texml_app_id: str


class TelnyxGateway(Protocol):
    async def create_insight(
        self, *, name: str, instructions: str, json_schema: dict[str, Any] | None
    ) -> str: ...

    async def create_insight_group(self, *, name: str) -> str: ...

    async def assign_insight_to_group(self, *, group_id: str, insight_id: str) -> None: ...

    async def create_assistant(
        self,
        *,
        name: str,
        model: str,
        instructions: str,
        greeting: str,
        voice: str,
        voice_speed: float,
        transcription_model: str,
        interruption: bool,
        time_limit_secs: int,
        idle_timeout_secs: int,
        insight_group_id: str,
        dynamic_variables: dict[str, str],
    ) -> Assistant: ...

    async def get_assistant(self, *, assistant_id: str) -> dict[str, Any]: ...

    async def update_assistant(
        self,
        *,
        assistant_id: str,
        model: str,
        instructions: str,
        greeting: str,
        voice: str,
        voice_speed: float,
        transcription_model: str,
        interruption: bool,
        time_limit_secs: int,
        idle_timeout_secs: int,
        dynamic_variables: dict[str, str],
    ) -> None:
        """Mutation is legal ONLY for scratch assistants — they have
        exactly one consumer (FR-TEST-3). Run assistants are never
        mutated (FR-ARCH layer table)."""
        ...

    async def synthesize_speech(self, *, voice: str, text: str, voice_speed: float) -> bytes:
        """FR-AGENT-11: voice preview audio, proxied so the API key
        never reaches the browser."""
        ...

    async def update_insight(
        self, *, insight_id: str, name: str, instructions: str, json_schema: dict[str, Any] | None
    ) -> None: ...

    async def unassign_insight_from_group(self, *, group_id: str, insight_id: str) -> None: ...

    async def find_conversations(self, *, assistant_id: str) -> list[dict[str, Any]]:
        """Recent conversations for one assistant, newest first. Used to
        join test calls to their conversations, since the dial response
        carries no conversation id (spike finding)."""
        ...

    async def place_call(
        self,
        *,
        connection_id: str,
        assistant_id: str,
        to: str,
        from_: str,
        variables: dict[str, str],
        metadata: dict[str, str],
        record: bool,
        status_callback: str,
        amd_status_callback: str,
    ) -> dict[str, Any]: ...

    async def get_conversation_messages(self, *, conversation_id: str) -> list[dict[str, Any]]: ...

    async def get_conversation_insights(self, *, conversation_id: str) -> list[dict[str, Any]]: ...

    async def delete_assistant(self, *, assistant_id: str) -> None: ...

    async def delete_texml_application(self, *, texml_app_id: str) -> None: ...

    async def get_recording_url(self, *, recording_id: str) -> str:
        """A FRESH playback URL for a stored recording id — minted per
        request, never persisted (FR-REC-3/4)."""
        ...

    # -- Console (Phase 6): account state read when the screen renders,
    # never cached in a background job (FR-CONSOLE-6/7, techstack).

    async def get_balance(self) -> dict[str, Any]:
        """The account balance object (available_credit, currency)."""
        ...

    async def list_phone_numbers(self) -> list[dict[str, Any]]:
        """Every number on the account, for inventory sync (FR-CONSOLE-5)."""
        ...

    async def search_available_numbers(
        self, *, country_code: str, limit: int
    ) -> list[dict[str, Any]]: ...

    async def order_number(self, *, phone_number: str) -> dict[str, Any]:
        """Order one specific number found via search (FR-CONSOLE-5)."""
        ...

    async def list_outbound_voice_profiles(self) -> list[dict[str, Any]]:
        """Enabled destinations live here (FR-CONSOLE-6; launch blocking
        condition 'destination country not enabled')."""
        ...

    # -- Billing (Phase 7). Groups must exist BEFORE the first billed
    # run: detail records are historical snapshots and never backfill
    # billing_group_id (FR-BILL-3).

    async def create_billing_group(self, *, name: str) -> str: ...

    async def set_number_billing_group(
        self, *, telnyx_phone_number_id: str, billing_group_id: str | None
    ) -> None:
        """Attach (or with None, detach) a number to a billing group, so
        cost attribution returns natively on the detail record."""
        ...

    async def list_detail_records(self, *, record_type: str, page_number: int) -> dict[str, Any]:
        """One page of cost records (FR-BILL-2). Returns the raw body —
        data[] plus meta{total_pages} — because the join key varies by
        record_type (spike findings §11) and the caller owns that."""
        ...
