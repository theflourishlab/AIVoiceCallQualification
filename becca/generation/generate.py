"""Brief -> agent content, via Anthropic structured output (FR-AGENT-4).

The single point where an incoherent structure can be PROPOSED, because
it is the only point where the structure arrives from outside. Decoding
through the domain constructor is the validation (FR-AGENT-3A); a
dangling ref is a generation failure, retried and never surfaced.
"""

from dataclasses import dataclass
from typing import Any, Protocol, cast

import anthropic
from anthropic.types import ToolChoiceToolParam, ToolParam

from becca.domain import scriptfmt
from becca.domain.codec import content_from_json, content_to_json
from becca.domain.model import AgentVersionContent, DanglingFieldRef, DuplicateFieldKey, TextBlock
from becca.generation.tool_schema import EMIT_AGENT_TOOL, REGENERATE_PROMPT, SYSTEM_PROMPT

_MODEL = "claude-sonnet-5"
_MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class GeneratedAgent:
    name: str
    content: AgentVersionContent


class GenerationFailed(Exception):
    """All attempts produced incoherent structure. Surfaced as a retry
    prompt to the user, never as the structural detail."""


def _spaced(content: AgentVersionContent) -> AgentVersionContent:
    """Bake section spacing into the STORED script (user requirement,
    14 Aug 2026): the prompt asks for blank lines between sections, but
    the model does not reliably produce them — so every generated and
    regenerated script is normalised here, deterministically, instead of
    hoping. Display-time patching remains only for pre-existing agents."""
    blocks = tuple(
        TextBlock(scriptfmt.ensure_blank_lines(b.content, first_block=i == 0))
        if isinstance(b, TextBlock)
        else b
        for i, b in enumerate(content.script_blocks)
    )
    return AgentVersionContent(fields=content.fields, script_blocks=blocks)


class Generator(Protocol):
    async def generate(self, brief: str) -> GeneratedAgent: ...

    async def regenerate(
        self, *, current: AgentVersionContent, problem: str
    ) -> AgentVersionContent:
        """FR-TEST-9: scoped to the described problem, preserving the
        field set unless the description requires changing it."""
        ...


class AnthropicGenerator:
    def __init__(self, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def generate(self, brief: str) -> GeneratedAgent:
        return await self._emit(system=SYSTEM_PROMPT, user=brief)

    async def regenerate(
        self, *, current: AgentVersionContent, problem: str
    ) -> AgentVersionContent:
        user = (
            "Current agent as JSON:\n"
            + str(content_to_json(current))
            + "\n\nWhat sounded wrong on the test call:\n"
            + problem
        )
        generated = await self._emit(system=REGENERATE_PROMPT, user=user)
        return generated.content

    async def _emit(self, *, system: str, user: str) -> GeneratedAgent:
        last_error: Exception | None = None
        for _ in range(_MAX_ATTEMPTS):
            message = await self._client.messages.create(
                model=_MODEL,
                max_tokens=4096,
                system=system,
                tools=[cast(ToolParam, EMIT_AGENT_TOOL)],
                tool_choice=ToolChoiceToolParam(type="tool", name="emit_agent"),
                messages=[{"role": "user", "content": user}],
            )
            payload: dict[str, Any] | None = None
            for block in message.content:
                if block.type == "tool_use" and block.name == "emit_agent":
                    assert isinstance(block.input, dict)
                    payload = block.input
            if payload is None:
                continue
            try:
                content = content_from_json(payload)
            except (DanglingFieldRef, DuplicateFieldKey) as exc:
                last_error = exc  # FR-AGENT-3A: retry, never surface
                continue
            return GeneratedAgent(
                name=str(payload.get("agent_name", "New agent")), content=_spaced(content)
            )
        raise GenerationFailed("generation kept producing incoherent structure") from last_error
