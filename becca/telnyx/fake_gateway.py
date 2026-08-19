"""In-memory Telnyx for development and tests.

TELNYX_MODE defaults to "fake", so nothing dials a real number unless a
human sets "real" deliberately — and even then, SD-13's allowlist in the
HTTP gateway guards non-production. Phase 0 keeps it minimal — realistic
ids and lifecycle
(including the non-cascading delete, FR-LAUNCH-6). Signed-webhook
delivery arrives with the Phase 1 test suite.
"""

import itertools
from typing import Any

from becca.telnyx.gateway import Assistant, TelnyxNotFound


class FakeTelnyxGateway:
    def __init__(self) -> None:
        self._seq = itertools.count(1)
        self.insights: dict[str, dict[str, Any]] = {}
        self.groups: dict[str, list[str]] = {}
        self.assistants: dict[str, dict[str, Any]] = {}
        self.texml_apps: set[str] = set()
        self.calls: list[dict[str, Any]] = []
        self.numbers: list[dict[str, Any]] = []
        self.billing_groups: dict[str, str] = {}
        self.previews: list[dict[str, Any]] = []

    def _id(self, prefix: str) -> str:
        return f"fake-{prefix}-{next(self._seq)}"

    async def create_insight(
        self, *, name: str, instructions: str, json_schema: dict[str, Any] | None
    ) -> str:
        insight_id = self._id("insight")
        self.insights[insight_id] = {
            "name": name,
            "instructions": instructions,
            "json_schema": json_schema,
        }
        return insight_id

    async def create_insight_group(self, *, name: str) -> str:
        group_id = self._id("group")
        self.groups[group_id] = []
        return group_id

    async def assign_insight_to_group(self, *, group_id: str, insight_id: str) -> None:
        self.groups[group_id].append(insight_id)

    async def create_assistant(
        self,
        *,
        name: str,
        model: str,
        instructions: str,
        greeting: str,
        voice: str,
        voice_speed: float = 1.0,
        transcription_model: str = "deepgram/flux",
        interruption: bool = True,
        time_limit_secs: int = 300,
        idle_timeout_secs: int = 10,
        insight_group_id: str,
        dynamic_variables: dict[str, str],
    ) -> Assistant:
        assistant_id = self._id("assistant")
        texml_app_id = self._id("texml-app")
        self.texml_apps.add(texml_app_id)
        self.assistants[assistant_id] = {
            "id": assistant_id,
            "name": name,
            "model": model,
            "instructions": instructions,
            "greeting": greeting,
            "voice": voice,
            "voice_speed": voice_speed,
            "transcription_model": transcription_model,
            "interruption": interruption,
            "time_limit_secs": time_limit_secs,
            "idle_timeout_secs": idle_timeout_secs,
            "insight_group_id": insight_group_id,
            "dynamic_variables": dynamic_variables,
            "telephony_settings": {"default_texml_app_id": texml_app_id},
        }
        return Assistant(id=assistant_id, default_texml_app_id=texml_app_id)

    async def get_assistant(self, *, assistant_id: str) -> dict[str, Any]:
        try:
            return self.assistants[assistant_id]
        except KeyError:
            raise TelnyxNotFound(404, "assistant not found") from None

    async def update_assistant(
        self,
        *,
        assistant_id: str,
        model: str,
        instructions: str,
        greeting: str,
        voice: str,
        voice_speed: float = 1.0,
        transcription_model: str = "deepgram/flux",
        interruption: bool = True,
        time_limit_secs: int = 300,
        idle_timeout_secs: int = 10,
        dynamic_variables: dict[str, str],
    ) -> None:
        a = await self.get_assistant(assistant_id=assistant_id)
        a.update(
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
        )

    async def synthesize_speech(self, *, voice: str, text: str, voice_speed: float) -> bytes:
        self.previews.append({"voice": voice, "text": text, "voice_speed": voice_speed})
        return b"ID3fake-mp3-preview"

    async def update_insight(
        self, *, insight_id: str, name: str, instructions: str, json_schema: dict[str, Any] | None
    ) -> None:
        if insight_id not in self.insights:
            raise TelnyxNotFound(404, "insight not found")
        self.insights[insight_id] = {
            "name": name,
            "instructions": instructions,
            "json_schema": json_schema,
        }

    async def unassign_insight_from_group(self, *, group_id: str, insight_id: str) -> None:
        if insight_id in self.groups.get(group_id, []):
            self.groups[group_id].remove(insight_id)

    async def find_conversations(self, *, assistant_id: str) -> list[dict[str, Any]]:
        """A conversation exists for every call placed to the assistant's
        TeXML app, newest first — mirroring the real join path."""
        try:
            texml_app = self.assistants[assistant_id]["telephony_settings"]["default_texml_app_id"]
        except KeyError:
            return []
        return [
            {
                "id": c["conversation_id"],
                "metadata": {
                    "assistant_id": assistant_id,
                    "call_session_id": c["call_session_id"],
                    "call_control_id": c["call_control_id"],
                },
            }
            for c in reversed(self.calls)
            if c["connection_id"] == texml_app
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
        call_sid = self._id("callsid")
        self.calls.append(
            {
                "call_session_id": self._id("session"),
                "conversation_id": self._id("conversation"),
                "call_control_id": call_sid,
                "connection_id": connection_id,
                "assistant_id": assistant_id,
                "to": to,
                "from": from_,
                "variables": variables,
                "metadata": metadata,
                "record": record,
            }
        )
        # Live finding (6 Aug 2026): the dial response is minimal — no
        # session or conversation id. Those arrive via the status callback
        # and the conversation's own metadata respectively.
        return {
            "call_sid": call_sid,
            "from": from_,
            "to": to,
            "status": "queued",
        }

    async def get_conversation_messages(self, *, conversation_id: str) -> list[dict[str, Any]]:
        return [
            {"role": "assistant", "text": "Good afternoon, is this Chidinma?", "created_at": "0:1"},
            {"role": "user", "text": "Yes, speaking.", "created_at": "0:04"},
        ]

    async def get_conversation_insights(self, *, conversation_id: str) -> list[dict[str, Any]]:
        """Mirror the live shape: data[].conversation_insights[] of
        {insight_id, result}, schema results as stringified JSON. Results
        cover the insights in the calling assistant's group."""
        call = next((c for c in self.calls if c["conversation_id"] == conversation_id), None)
        if call is None:
            return []
        assistant = next(
            (
                a
                for a in self.assistants.values()
                if a["telephony_settings"]["default_texml_app_id"] == call["connection_id"]
            ),
            None,
        )
        if assistant is None:
            return []
        group = self.groups.get(assistant["insight_group_id"], [])
        results = []
        for insight_id in group:
            spec = self.insights[insight_id]
            schema = spec.get("json_schema")
            if schema:
                key = next(iter(schema["properties"]))
                value = schema["properties"][key].get("enum", ["fake"])[0]
                results.append({"insight_id": insight_id, "result": f'{{"{key}": "{value}"}}'})
            else:
                results.append(
                    {
                        "insight_id": insight_id,
                        "result": f"Fake summary after {len(self.calls)} call(s).",
                    }
                )
        return [{"status": "completed", "conversation_insights": results}]

    async def delete_assistant(self, *, assistant_id: str) -> None:
        if assistant_id not in self.assistants:
            raise TelnyxNotFound(404, "assistant not found")
        # Deliberately does NOT remove the TeXML app: the real account does
        # not cascade (FR-LAUNCH-6), and the fake preserves that trap.
        del self.assistants[assistant_id]

    async def delete_texml_application(self, *, texml_app_id: str) -> None:
        if texml_app_id not in self.texml_apps:
            raise TelnyxNotFound(404, "texml application not found")
        self.texml_apps.discard(texml_app_id)

    async def get_recording_url(self, *, recording_id: str) -> str:
        return f"https://fake.telnyx.example/recordings/{recording_id}.mp3"

    # -- Console (Phase 6). Enough account state to demo the console
    # end-to-end without Telnyx: a balance, two numbers, one profile.

    async def get_balance(self) -> dict[str, Any]:
        return {"available_credit": "42.5000", "balance": "42.5000", "currency": "USD"}

    async def list_phone_numbers(self) -> list[dict[str, Any]]:
        if not self.numbers:
            self.numbers = [
                {
                    "id": self._id("number"),
                    "phone_number": "+2342093940544",
                    "status": "active",
                    "country_iso_alpha2": "NG",
                },
                {
                    "id": self._id("number"),
                    "phone_number": "+2347000000001",
                    "status": "active",
                    "country_iso_alpha2": "NG",
                },
            ]
        return self.numbers

    async def search_available_numbers(
        self, *, country_code: str, limit: int
    ) -> list[dict[str, Any]]:
        return [
            {"phone_number": f"+23412088{4100 + next(self._seq):04d}", "region": country_code}
            for _ in range(limit)
        ]

    async def order_number(self, *, phone_number: str) -> dict[str, Any]:
        await self.list_phone_numbers()  # seed first, so the order appends
        record = {
            "id": self._id("number"),
            "phone_number": phone_number,
            "status": "active",
            "country_iso_alpha2": "NG",
        }
        self.numbers.append(record)
        return {"id": self._id("order"), "status": "success"}

    async def create_billing_group(self, *, name: str) -> str:
        group_id = self._id("bg")
        self.billing_groups[group_id] = name
        return group_id

    async def set_number_billing_group(
        self, *, telnyx_phone_number_id: str, billing_group_id: str | None
    ) -> None:
        if billing_group_id is not None and billing_group_id not in self.billing_groups:
            raise TelnyxNotFound(404, "billing group not found")
        for n in self.numbers:
            if n["id"] == telnyx_phone_number_id:
                n["billing_group_id"] = billing_group_id
                return
        raise TelnyxNotFound(404, "phone number not found")

    async def list_detail_records(self, *, record_type: str, page_number: int) -> dict[str, Any]:
        """Mirror the live shapes (spike findings §11/§11a): per placed
        call, an ai-voice-assistant record ($0.05/min), the sip-trunking
        PSTN leg ($0.117/min — the largest component), a call-control
        leg and an amd invocation. Other types return empty."""
        per_call = {
            "ai-voice-assistant": ("telnyx_session_id", "0.1"),
            "sip-trunking": ("telnyx_session_id", "0.234"),
            "call-control": ("telnyx_session_id", "0.004"),
            "amd": ("call_session_id", "0.0065"),
        }
        if record_type in per_call:
            key, cost = per_call[record_type]
            data = [
                {
                    "record_type": record_type,
                    key: c["call_session_id"],
                    "cost": cost,
                    "billed_sec": 120,
                    "currency": "USD",
                }
                for c in self.calls
            ]
        else:
            data = []
        return {"data": data, "meta": {"total_pages": 1, "page_number": page_number}}

    async def list_outbound_voice_profiles(self) -> list[dict[str, Any]]:
        return [
            {
                "id": self._id("ovp"),
                "name": "fake-profile",
                "whitelisted_destinations": ["NG", "GB"],
                "concurrent_call_limit": 10,
            }
        ]
