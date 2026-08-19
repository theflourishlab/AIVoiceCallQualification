"""The judged half of the rubric: one model call per transcript.

The judge reads the SAME instruction sheet the assistant received (with
real values substituted) plus the numbered transcript, and reports
violations with quoted evidence — the criteria definitions come straight
from rubric.CRITERIA, so editing a definition there changes what the
judge enforces. Violations referencing unknown criteria or out-of-range
turns are dropped, not surfaced: an invalid claim is not evidence.
"""

from typing import Any, Protocol, cast

import anthropic
from anthropic.types import ToolChoiceToolParam, ToolParam

from becca.evals.rubric import JUDGED_CRITERIA, Violation

_MODEL = "claude-sonnet-5"
_MAX_ATTEMPTS = 3


class JudgeFailed(Exception):
    """All attempts produced no usable scorecard."""


class Judge(Protocol):
    async def score(
        self, *, instructions: str, messages: list[dict[str, Any]]
    ) -> tuple[list[Violation], str]:
        """(violations, overall impression) for one transcript."""
        ...


def _tool_schema() -> dict[str, Any]:
    return {
        "name": "emit_scorecard",
        "description": "Report every violation found, with evidence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "violations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "criterion": {
                                "type": "string",
                                "enum": [c.id for c in JUDGED_CRITERIA],
                            },
                            "turn": {
                                "type": "integer",
                                "description": "The [n] index of the offending turn.",
                            },
                            "quote": {
                                "type": "string",
                                "description": "The offending text, verbatim.",
                            },
                            "note": {
                                "type": "string",
                                "description": "One line: why this violates the criterion.",
                            },
                        },
                        "required": ["criterion", "turn", "quote", "note"],
                    },
                },
                "overall": {
                    "type": "string",
                    "description": "2-3 sentences: how the call felt as a whole.",
                },
            },
            "required": ["violations", "overall"],
        },
    }


def _system_prompt() -> str:
    criteria = "\n".join(f"- {c.id}: {c.definition}" for c in JUDGED_CRITERIA)
    return (
        "You are auditing the transcript of an OUTBOUND phone call placed"
        " by a voice agent. You are given the exact instruction sheet the"
        " agent received (behavioural rules, the real values it held, and"
        " its call plan) and the transcript with numbered turns.\n\n"
        "Report VIOLATIONS of these criteria — concrete divergences"
        " between what the agent said and what its instruction sheet"
        " entitled or forbade it to say:\n\n"
        f"{criteria}\n\n"
        "Rules of evidence:\n"
        "- Every violation cites one turn and quotes it verbatim. If you"
        " cannot point at a turn, it is not a violation.\n"
        "- Judge only the agent's turns. The contact's side (including"
        " transcription mishears in EITHER side's text) is context, not"
        " conduct — except where the agent adopts a mishear.\n"
        "- The plan is behavioural direction, not lines to recite:"
        " different phrasing is fine; skipped, reordered, or invented"
        " substance is not.\n"
        "- A clean call has zero violations. Do not manufacture findings"
        " to seem thorough, and do not excuse real ones.\n"
        "- The 'overall' field is your only place for impressions;"
        " violations are for facts."
    )


def render_transcript(messages: list[dict[str, Any]]) -> str:
    lines = []
    for i, m in enumerate(messages):
        role = "AGENT" if m.get("role") == "assistant" else "CONTACT"
        text = str(m.get("text") or "").strip()
        if text:
            lines.append(f"[{i}] {role}: {text}")
    return "\n".join(lines)


def _parse(payload: dict[str, Any], n_turns: int) -> tuple[list[Violation], str]:
    valid_ids = {c.id for c in JUDGED_CRITERIA}
    violations = []
    for v in payload.get("violations", []):
        if not isinstance(v, dict) or v.get("criterion") not in valid_ids:
            continue
        turn = v.get("turn")
        if not isinstance(turn, int) or not 0 <= turn < n_turns:
            continue
        violations.append(
            Violation(str(v["criterion"]), turn, str(v.get("quote", "")), str(v.get("note", "")))
        )
    return violations, str(payload.get("overall", ""))


class AnthropicJudge:
    def __init__(self, api_key: str, model: str = _MODEL) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def score(
        self, *, instructions: str, messages: list[dict[str, Any]]
    ) -> tuple[list[Violation], str]:
        user = (
            "INSTRUCTION SHEET THE AGENT RECEIVED (values substituted):\n"
            "----------------------------------------------------------\n"
            f"{instructions}\n"
            "----------------------------------------------------------\n\n"
            "TRANSCRIPT:\n"
            f"{render_transcript(messages)}"
        )
        for _ in range(_MAX_ATTEMPTS):
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=_system_prompt(),
                tools=[cast(ToolParam, _tool_schema())],
                tool_choice=ToolChoiceToolParam(type="tool", name="emit_scorecard"),
                messages=[{"role": "user", "content": user}],
            )
            for block in message.content:
                if block.type == "tool_use" and block.name == "emit_scorecard":
                    assert isinstance(block.input, dict)
                    return _parse(block.input, len(messages))
        raise JudgeFailed("judge kept returning no scorecard")
