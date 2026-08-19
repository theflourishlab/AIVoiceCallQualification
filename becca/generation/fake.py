"""Deterministic generator for tests and keyless dev.

Emits the FRD §4.1 shape regardless of the brief, with the brief's first
line as the agent name. Exercises the same decode boundary as the real
generator.
"""

from dataclasses import replace

from becca.domain.codec import content_from_json
from becca.domain.model import AgentVersionContent, TextBlock
from becca.generation.generate import GeneratedAgent


class FakeGenerator:
    async def regenerate(
        self, *, current: AgentVersionContent, problem: str
    ) -> AgentVersionContent:
        """Preserves the field set; appends a scoped script tweak so the
        change is visible and versionable."""
        note = TextBlock(content=f" (Adjusted: {problem.strip()[:60]})")
        return replace(current, script_blocks=(*current.script_blocks, note))

    async def generate(self, brief: str) -> GeneratedAgent:
        name = (brief.strip().splitlines() or ["New agent"])[0][:60] or "New agent"
        content = content_from_json(
            {
                "fields": [
                    {
                        "id": 1,
                        "key": "first_name",
                        "kind": "input",
                        "required": True,
                        "type": "text",
                    },
                    {
                        "id": 2,
                        "key": "visit_date",
                        "kind": "input",
                        "required": True,
                        "type": "text",
                    },
                    {
                        "id": 3,
                        "key": "still_attending",
                        "kind": "output",
                        "required": True,
                        "type": "enum",
                        "values": ["yes", "no", "unsure"],
                        "instructions": "Is the contact still attending?",
                    },
                    {
                        "id": 4,
                        "key": "summary",
                        "kind": "output",
                        "required": True,
                        "type": "text",
                        "instructions": "Two sentences, what was agreed",
                    },
                ],
                "script_blocks": [
                    {
                        "type": "text",
                        "content": "Role. You are calling on behalf of the business, warm and"
                        " brief.\n\nOpening. Greet ",
                    },
                    {"type": "field_ref", "field_id": 1},
                    {
                        "type": "text",
                        "content": " by name, identify the business and state the purpose in"
                        " one sentence.\n\nConfirm. Remind them their visit is booked for ",
                    },
                    {"type": "field_ref", "field_id": 2},
                    {
                        "type": "text",
                        "content": " and ask whether they are still attending.\n\nClose. Thank"
                        " them by name and end warmly.",
                    },
                ],
            }
        )
        return GeneratedAgent(name=name, content=content)
