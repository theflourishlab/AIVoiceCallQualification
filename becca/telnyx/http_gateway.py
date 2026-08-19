"""The real Telnyx gateway. One function per endpoint, nothing clever.

This module is the one place Becca's own dial rules live:

- the AMD block is sent on every outbound call (FR-DISPATCH-4) — Telnyx
  defaults it off, and off means the agent converses with answerphones
- recording_settings.enabled is pinned false on every assistant we create
  (FR-AGENT-12); recording is decided per call by the Record flag (FR-REC-2)
- conversation metadata is string-typed (FR-DATA-3)

Retry policy (SD-16): failures before the request was sent are retried;
4xx other than 429 never are; a dial whose connection dropped after send
raises AmbiguousDialError and must be reconciled, never blindly retried.
"""

from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from becca.telnyx.gateway import (
    AmbiguousDialError,
    Assistant,
    DialRefused,
    TelnyxError,
    TelnyxNotFound,
)

# The request never reached Telnyx: nothing happened, retrying is free.
_PRE_SEND_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)

# Tuned below Telnyx's 30000 default so a human answer is not held waiting
# for the full detection window (FR-DISPATCH-4).
_MACHINE_DETECTION_TIMEOUT_MS = 15000


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, _PRE_SEND_ERRORS):
        return True
    if isinstance(exc, TelnyxError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


_retrying = retry(
    retry=retry_if_exception(_retryable),
    stop=stop_after_attempt(4),
    wait=wait_exponential_jitter(initial=0.5, max=8),
    reraise=True,
)


def _normalise_number(number: str) -> str:
    """Comparison form for the allowlist: digits and +, nothing else."""
    return "".join(c for c in number if c.isdigit() or c == "+")


class HttpTelnyxGateway:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.telnyx.com/v2",
        environment: str = "dev",
        dial_allowlist: frozenset[str] = frozenset(),
    ) -> None:
        # Fail closed (SD-13): a constructor that is not told it is
        # production, with no allowlist, can dial nobody.
        self._environment = environment
        self._dial_allowlist = frozenset(_normalise_number(n) for n in dial_allowlist)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @_retrying
    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        r = await self._client.request(method, path, **kwargs)
        if r.status_code == 404:
            raise TelnyxNotFound(r.status_code, r.text)
        if r.status_code >= 400:
            raise TelnyxError(r.status_code, r.text)
        if not r.content:
            return {}
        body: dict[str, Any] = r.json()
        return body

    async def create_insight(
        self, *, name: str, instructions: str, json_schema: dict[str, Any] | None
    ) -> str:
        payload: dict[str, Any] = {"name": name, "instructions": instructions}
        if json_schema is not None:
            payload["json_schema"] = json_schema
        body = await self._request("POST", "/ai/conversations/insights", json=payload)
        return str(body["data"]["id"])

    async def create_insight_group(self, *, name: str) -> str:
        body = await self._request("POST", "/ai/conversations/insight-groups", json={"name": name})
        return str(body["data"]["id"])

    async def assign_insight_to_group(self, *, group_id: str, insight_id: str) -> None:
        await self._request(
            "POST", f"/ai/conversations/insight-groups/{group_id}/insights/{insight_id}/assign"
        )

    def _behaviour_body(
        self,
        *,
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
    ) -> dict[str, Any]:
        """The FR-AGENT-9 field mapping, shared by create and update so
        the sync re-asserts every behaviour setting on every test call."""
        return {
            # Live finding: the model listing is not the eligibility
            # list — Meta-Llama-3.1-70B is listed yet 422s here. 14
            # of 25 listed models attach (probed 13 Aug 2026).
            "model": model,
            "instructions": instructions,
            "greeting": greeting,
            # Voice ids verbatim from GET /text-to-speech/voices, never
            # reassembled (FR-AGENT-10).
            "voice_settings": {"voice": voice, "voice_speed": voice_speed},
            "transcription": {"model": transcription_model},
            "interruption_settings": {"enable": interruption},
            "dynamic_variables": dynamic_variables,
            # Insights do not run without transcripts (FR-AGENT-9) —
            # pinned true, never a per-agent choice.
            "privacy_settings": {"data_retention": True},
            "telephony_settings": {
                # Telnyx defaults this true; unset would record everything.
                # false hands the decision to the per-call Record flag
                # (FR-AGENT-12, FR-REC-2).
                "recording_settings": {"enabled": False},
                # Hang up after silence so a finished conversation does
                # not keep the line open and billing (FR-AGENT-9 —
                # live test: the agent said goodbye and stayed on).
                "user_idle_timeout_secs": idle_timeout_secs,
                "time_limit_secs": time_limit_secs,
            },
        }

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
    ) -> Assistant:
        body = await self._request(
            "POST",
            "/ai/assistants",
            json={
                "name": name,
                "insight_settings": {"insight_group_id": insight_group_id},
                **self._behaviour_body(
                    model=model,
                    instructions=instructions,
                    greeting=greeting,
                    voice=voice,
                    voice_speed=voice_speed,
                    transcription_model=transcription_model,
                    interruption=interruption,
                    time_limit_secs=time_limit_secs,
                    idle_timeout_secs=idle_timeout_secs,
                    dynamic_variables=dynamic_variables,
                ),
            },
        )
        # Live finding: create returns the assistant object unwrapped,
        # unlike the list endpoint which wraps in "data".
        data = body.get("data", body)
        return Assistant(
            id=str(data["id"]),
            default_texml_app_id=str(data["telephony_settings"]["default_texml_app_id"]),
        )

    async def get_assistant(self, *, assistant_id: str) -> dict[str, Any]:
        body = await self._request("GET", f"/ai/assistants/{assistant_id}")
        data: dict[str, Any] = body.get("data", body)
        return data

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
        # POST, not PATCH — the update endpoint per the verified registry.
        await self._request(
            "POST",
            f"/ai/assistants/{assistant_id}",
            json=self._behaviour_body(
                model=model,
                instructions=instructions,
                greeting=greeting,
                voice=voice,
                voice_speed=voice_speed,
                transcription_model=transcription_model,
                interruption=interruption,
                time_limit_secs=time_limit_secs,
                idle_timeout_secs=idle_timeout_secs,
                dynamic_variables=dynamic_variables,
            ),
        )

    async def synthesize_speech(self, *, voice: str, text: str, voice_speed: float) -> bytes:
        """FR-AGENT-11: the proxied voice preview. Fixed sample text +
        Telnyx-side caching make repeat previews cache hits. Bytes, not
        _request: the response is audio, not JSON."""
        response = await self._client.post(
            "/text-to-speech/speech",
            json={"text": text, "voice": voice, "voice_speed": voice_speed},
        )
        if response.status_code >= 400:
            raise TelnyxError(response.status_code, response.text[:300])
        return response.content

    async def update_insight(
        self, *, insight_id: str, name: str, instructions: str, json_schema: dict[str, Any] | None
    ) -> None:
        payload: dict[str, Any] = {"name": name, "instructions": instructions}
        if json_schema is not None:
            payload["json_schema"] = json_schema
        await self._request("PUT", f"/ai/conversations/insights/{insight_id}", json=payload)

    async def unassign_insight_from_group(self, *, group_id: str, insight_id: str) -> None:
        # Assign is POST on this path; unassign assumed DELETE on the same
        # path (registry says "unassign" without a verb — verify on first
        # live use and correct here if it 405s).
        await self._request(
            "DELETE", f"/ai/conversations/insight-groups/{group_id}/insights/{insight_id}/assign"
        )

    async def find_conversations(self, *, assistant_id: str) -> list[dict[str, Any]]:
        body = await self._request("GET", "/ai/conversations", params={"limit": 20})
        data: list[dict[str, Any]] = body.get("data", [])
        return [
            c
            for c in data
            if isinstance(c.get("metadata"), dict)
            and c["metadata"].get("assistant_id") == assistant_id
        ]

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
    ) -> dict[str, Any]:
        # SD-13, resolved 12 Aug 2026: outside production, refuse any
        # destination not explicitly allowlisted. Separate API keys per
        # environment do NOT protect here — every key in the one Telnyx
        # account shares the same numbers and balance.
        if self._environment != "production" and _normalise_number(to) not in self._dial_allowlist:
            raise DialRefused(to, self._environment)
        payload = {
            "To": to,
            "From": from_,
            # Live finding: required (10004) even though the connection is
            # the assistant's own TeXML app.
            "AIAssistantId": assistant_id,
            "AIAssistantDynamicVariables": {k: str(v) for k, v in variables.items()},
            # Metadata is string-typed; integers get coerced (FR-DATA-3).
            "ConversationMetadata": {k: str(v) for k, v in metadata.items()},
            # The AMD block. All defaults are off; silence means full
            # conversations with answerphones (FR-DISPATCH-4).
            "MachineDetection": "DetectMessageEnd",
            "DetectionMode": "Premium",
            "AsyncAmd": True,
            "MachineDetectionTimeout": _MACHINE_DETECTION_TIMEOUT_MS,
            "Record": record,  # FR-REC-2: the client's setting at dial time
            "Timeout": 30,
        }
        # Never send empty callback URLs — a dial with StatusCallback=""
        # was accepted (queued, call_sid) yet never connected, and its
        # empty conversation later vanished from the list (observed live
        # 11 Aug 2026). Omit the params entirely when there is no URL.
        if status_callback:
            payload["StatusCallback"] = status_callback
        if amd_status_callback:
            payload["AsyncAmdStatusCallback"] = amd_status_callback
        try:
            r = await self._client.post(f"/texml/ai_calls/{connection_id}", json=payload)
        except _PRE_SEND_ERRORS:
            # Telnyx never saw it; the dispatcher may safely retry.
            raise
        except httpx.HTTPError as exc:
            # Sent, answer lost: the call may exist. Reconcile, never redial.
            raise AmbiguousDialError(str(exc)) from exc
        if r.status_code >= 400:
            raise TelnyxError(r.status_code, r.text)
        body: dict[str, Any] = r.json()
        return body

    async def get_conversation_messages(self, *, conversation_id: str) -> list[dict[str, Any]]:
        body = await self._request("GET", f"/ai/conversations/{conversation_id}/messages")
        data: list[dict[str, Any]] = body.get("data", [])
        return data

    async def get_conversation_insights(self, *, conversation_id: str) -> list[dict[str, Any]]:
        # Doubled plural is the real path shape (prototype API registry,
        # verified against spec v2.24.0).
        body = await self._request(
            "GET", f"/ai/conversations/{conversation_id}/conversations-insights"
        )
        data: list[dict[str, Any]] = body.get("data", [])
        return data

    async def delete_assistant(self, *, assistant_id: str) -> None:
        await self._request("DELETE", f"/ai/assistants/{assistant_id}")

    async def delete_texml_application(self, *, texml_app_id: str) -> None:
        # Deleting an assistant does not cascade (FR-LAUNCH-6, tested):
        # the TeXML application must be deleted separately or it dangles.
        await self._request("DELETE", f"/texml_applications/{texml_app_id}")

    # -- Console (Phase 6). None of these five have run against the live
    # account yet; paths and shapes follow the Telnyx v2 spec the same way
    # the verified endpoints did. Verify each on first live render and
    # correct here if one 404s — the console degrades to "unavailable"
    # rather than erroring (services/console.py catches TelnyxError).

    async def get_balance(self) -> dict[str, Any]:
        body = await self._request("GET", "/balance")
        data: dict[str, Any] = body.get("data", body)
        return data

    async def list_phone_numbers(self) -> list[dict[str, Any]]:
        # One page is plenty: the account holds single-digit numbers.
        body = await self._request("GET", "/phone_numbers", params={"page[size]": 250})
        data: list[dict[str, Any]] = body.get("data", [])
        return data

    async def search_available_numbers(
        self, *, country_code: str, limit: int
    ) -> list[dict[str, Any]]:
        body = await self._request(
            "GET",
            "/available_phone_numbers",
            params={"filter[country_code]": country_code, "filter[limit]": limit},
        )
        data: list[dict[str, Any]] = body.get("data", [])
        return data

    async def order_number(self, *, phone_number: str) -> dict[str, Any]:
        body = await self._request(
            "POST", "/number_orders", json={"phone_numbers": [{"phone_number": phone_number}]}
        )
        data: dict[str, Any] = body.get("data", body)
        return data

    async def list_outbound_voice_profiles(self) -> list[dict[str, Any]]:
        body = await self._request("GET", "/outbound_voice_profiles")
        data: list[dict[str, Any]] = body.get("data", [])
        return data

    # -- Billing (Phase 7). Both untested live, same caveat as the
    # console endpoints above. PATCH /phone_numbers/{id} with a
    # billing_group_id is the attachment mechanism the FRD's spike
    # confirmed exists on the record (FR-BILL-3).

    async def create_billing_group(self, *, name: str) -> str:
        body = await self._request("POST", "/billing_groups", json={"name": name})
        data: dict[str, Any] = body.get("data", body)
        return str(data["id"])

    async def set_number_billing_group(
        self, *, telnyx_phone_number_id: str, billing_group_id: str | None
    ) -> None:
        await self._request(
            "PATCH",
            f"/phone_numbers/{telnyx_phone_number_id}",
            json={"billing_group_id": billing_group_id},
        )

    async def list_detail_records(self, *, record_type: str, page_number: int) -> dict[str, Any]:
        # Live-verified 12 Aug 2026 (spike findings §11).
        body = await self._request(
            "GET",
            "/detail_records",
            params={
                "filter[record_type]": record_type,
                "page[size]": 250,
                "page[number]": page_number,
            },
        )
        return body

    async def get_recording_url(self, *, recording_id: str) -> str:
        """Fresh per request, never stored (FR-REC-3/4). UNTESTED LIVE:
        no recorded call has run yet — verify download_urls shape on the
        first real recording before trusting this in production."""
        body = await self._request("GET", f"/recordings/{recording_id}")
        urls = body.get("data", {}).get("download_urls", {})
        url = urls.get("mp3") or urls.get("wav") or ""
        if not url:
            raise TelnyxError(404, f"recording {recording_id} has no download url yet")
        return str(url)
