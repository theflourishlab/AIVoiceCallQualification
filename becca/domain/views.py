"""The contract and the schema are questions, not documents (FR-AGENT-2).

Both are computed from the field list every time they are needed, so they
cannot disagree with it. Storing either would reintroduce the drift the
model removes.
"""

from becca.domain.model import AgentVersionContent, Field


def variable_contract(content: AgentVersionContent) -> tuple[Field, ...]:
    """Which columns the contact list must supply: fields where kind = input."""
    return tuple(f for f in content.fields if f.kind == "input")


def insight_schema(content: AgentVersionContent) -> tuple[Field, ...]:
    """What gets extracted after the call: fields where kind = output.

    Frozen at launch for the life of the run (FR-LAUNCH-7) — but the
    freeze is a lifecycle rule, not a property of this view.
    """
    return tuple(f for f in content.fields if f.kind == "output")
