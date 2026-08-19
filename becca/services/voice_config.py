"""Per-agent voice & behaviour (FR-AGENT-9/10/11) — issue #1 strand 3.

The agent row stores only OVERRIDES (voice_config jsonb, absent keys =
defaults); this module owns resolution and clamping, so every consumer
— scratch sync, launch, the screen — sees one EffectiveVoiceConfig and
nothing ever writes an out-of-range value to Telnyx.

The catalogs are curated, not the raw 4,512-voice list: every entry was
auditioned or probed live (13 Aug 2026). Rachael (19 Aug 2026) is the
account's own hosted Ultra voice ("RACHAEL VOX RED", is_platform=false)
and the deployment default. Voice ids verbatim per
FR-AGENT-10. Conversation models carry the latency ground truth from
the eval rounds so the choice is informed, not vibes. Transcription
models are the five that accepted an assistant update when probed —
the FRD's "12-value enum" is not published anywhere queryable.
"""

from dataclasses import dataclass
from typing import Any

from becca.config import Settings

# ------------------------------------------------------------ catalogs


@dataclass(frozen=True)
class VoiceOption:
    id: str  # verbatim from GET /text-to-speech/voices (FR-AGENT-10)
    name: str
    gender: str
    blurb: str


VOICE_CATALOG: tuple[VoiceOption, ...] = (
    VoiceOption(
        "Telnyx.Ultra.81d6df1d-6175-4f24-9641-76d14b6d67cc",
        "Rachael",
        "Female",
        "Becca's own voice — hosted on the account (default)",
    ),
    VoiceOption(
        "Telnyx.Ultra.2747b6cf-fa34-460c-97db-267566918881",
        "Allie",
        "Female",
        "Natural conversationalist — confident, approachable",
    ),
    VoiceOption(
        "Telnyx.Ultra.1242fb95-7ddd-44ac-8a05-9e8a22a6137d",
        "Cindy",
        "Female",
        "Receptionist — smooth, welcoming, frontline",
    ),
    VoiceOption(
        "Telnyx.Ultra.02a924f6-bb49-4177-8fbb-52238c5056d6",
        "Maeve",
        "Female",
        "Steady host — gentle, patient, relaxed",
    ),
    VoiceOption(
        "Telnyx.Ultra.0d2162c2-2fe9-40a7-b3c1-43eab576a64b",
        "Shelly",
        "Female",
        "Warm companion — bright and friendly",
    ),
    VoiceOption(
        "Telnyx.Ultra.01eaafa9-308a-4276-a017-6ab0cf061b1f",
        "Clara",
        "Female",
        "Instructor — clear, precise, professional",
    ),
    VoiceOption(
        "Telnyx.Ultra.13524ffb-a918-499a-ae97-c98c7c4408c4",
        "Barry",
        "Male",
        "Helper — inviting, friendly support",
    ),
    VoiceOption(
        "Telnyx.Ultra.a167e0f3-df7e-4d52-a9c3-f949145efdab",
        "Blake",
        "Male",
        "Helpful agent — energetic, engaging",
    ),
    VoiceOption(
        "Telnyx.Ultra.bbee10a8-4f08-4c5c-8282-e69299115055",
        "Ben",
        "Male",
        "Helpful man — natural, slightly raspy, easygoing",
    ),
)


@dataclass(frozen=True)
class ModelOption:
    id: str
    label: str
    note: str


CONVERSATION_MODELS: tuple[ModelOption, ...] = (
    ModelOption(
        "anthropic/claude-haiku-4-5",
        "Claude Haiku 4.5",
        "Best instruction-following; warm closes · ~1.4s/turn",
    ),
    ModelOption(
        "moonshotai/Kimi-K2.6",
        "Kimi K2.6",
        "Fastest turns (~0.8s) · looser instruction-following",
    ),
    ModelOption(
        "openai/gpt-4.1",
        "GPT-4.1",
        "Untested on live calls",
    ),
    ModelOption(
        "openai/gpt-5.4-mini",
        "GPT-5.4 mini",
        "Tersest turns · abrupt closes; latency spikes to ~2s",
    ),
)

TRANSCRIPTION_MODELS: tuple[tuple[str, str], ...] = (
    ("deepgram/flux", "Deepgram Flux — best for accented English (default)"),
    ("deepgram/nova-3", "Deepgram Nova-3"),
    ("deepgram/nova-2", "Deepgram Nova-2"),
    ("distil-whisper/distil-large-v2", "Distil-Whisper large-v2"),
    ("openai/whisper-large-v3-turbo", "Whisper large-v3 turbo"),
)

# --------------------------------------------------------- resolution

_DEFAULT_SPEED = 1.0
_DEFAULT_STT = "deepgram/flux"
_DEFAULT_INTERRUPTION = True
_DEFAULT_TIME_LIMIT = 300
_DEFAULT_IDLE = 10


@dataclass(frozen=True)
class EffectiveVoiceConfig:
    """What an assistant is actually built with. data_retention is not
    here on purpose: it is pinned true (no insights without it) and the
    screen shows it as a fact, not a choice."""

    model: str
    voice: str
    voice_speed: float
    transcription_model: str
    interruption: bool
    time_limit_secs: int
    idle_timeout_secs: int


def resolve(overrides: dict[str, Any] | None, settings: Settings) -> EffectiveVoiceConfig:
    o = overrides or {}
    return EffectiveVoiceConfig(
        model=str(o.get("model") or settings.assistant_conversation_model),
        voice=str(o.get("voice") or settings.assistant_voice),
        voice_speed=_clamp_speed(o.get("voice_speed", _DEFAULT_SPEED)),
        transcription_model=_known_stt(o.get("transcription_model")),
        interruption=bool(o.get("interruption", _DEFAULT_INTERRUPTION)),
        time_limit_secs=_clamp_time_limit(o.get("time_limit_secs", _DEFAULT_TIME_LIMIT)),
        idle_timeout_secs=_clamp_idle(o.get("idle_timeout_secs", _DEFAULT_IDLE)),
    )


def _clamp_speed(value: Any) -> float:
    # FR-AGENT-9: assistant range 0.25-2.0.
    try:
        return min(2.0, max(0.25, float(value)))
    except TypeError, ValueError:
        return 1.0


def _clamp_time_limit(value: Any) -> int:
    # FR-AGENT-9: 30-14400.
    try:
        return min(14400, max(30, int(value)))
    except TypeError, ValueError:
        return _DEFAULT_TIME_LIMIT


def _clamp_idle(value: Any) -> int:
    try:
        return min(600, max(5, int(value)))
    except TypeError, ValueError:
        return _DEFAULT_IDLE


def _known_stt(value: Any) -> str:
    known = {m for m, _ in TRANSCRIPTION_MODELS}
    return str(value) if value in known else _DEFAULT_STT


def overrides_from_form(form: dict[str, str]) -> dict[str, Any]:
    """Form -> the stored override dict. Everything clamped on the way
    in AND resolved out (resolve clamps again), so a hand-posted form
    cannot store an out-of-range value."""
    out: dict[str, Any] = {}
    if form.get("voice"):
        out["voice"] = form["voice"]
    if form.get("model"):
        out["model"] = form["model"]
    if form.get("voice_speed"):
        out["voice_speed"] = _clamp_speed(form["voice_speed"])
    if form.get("transcription_model"):
        out["transcription_model"] = _known_stt(form["transcription_model"])
    out["interruption"] = form.get("interruption") == "on"
    if form.get("time_limit_secs"):
        out["time_limit_secs"] = _clamp_time_limit(form["time_limit_secs"])
    if form.get("idle_timeout_secs"):
        out["idle_timeout_secs"] = _clamp_idle(form["idle_timeout_secs"])
    return out


# FR-AGENT-11: the fixed preview sentence — constant, so Telnyx's cache
# makes repeat previews free; the preview speed range is deliberately
# narrower than the assistant's.
PREVIEW_TEXT = (
    "Hello, good day! This is a quick call on behalf of your business — "
    "I just wanted to check in and hear how you are doing today."
)


def clamp_preview_speed(value: Any) -> float:
    try:
        return min(2.0, max(0.5, float(value)))
    except TypeError, ValueError:
        return 1.0
