"""Serialising to Telnyx is a pure function of the artifact (FR-AGENT-3C).

Because the input is coherent by construction, the output cannot be
otherwise. Nothing here validates; it only renders. Mustache syntax
exists only in these rendered strings — it never appears in the
interface (FR-AGENT-3B1).
"""

from typing import Any

from becca.domain.model import AgentVersionContent, FieldRef
from becca.domain.views import insight_schema, variable_contract


def render_instructions(content: AgentVersionContent) -> str:
    """script_blocks -> the mustache instructions string."""
    parts: list[str] = []
    for block in content.script_blocks:
        if isinstance(block, FieldRef):
            parts.append("{{" + content.field_by_id(block.field_id).key + "}}")
        else:
            parts.append(block.content)
    return "".join(parts)


# The behavioural frame around the script. Without it the assistant LLM
# receives spoken prose as its entire system prompt, does not know it is
# the CALLER, and plays both sides — observed live on a test call
# (11 Aug 2026): it answered its own questions in the contact's name and
# improvised details the script never contained.
_ASSISTANT_PREAMBLE = """\
You are a voice agent placing an OUTBOUND phone call on behalf of a \
business. The person who answers is the contact, a customer. You are the \
caller; the contact is the other person.

The call opened with you saying hello. Once the contact responds, begin \
the Opening described below.

The call plan below is BEHAVIOURAL DIRECTION, not lines to recite — \
phrase everything naturally in your own words, differently from call to \
call, the way a warm, competent human caller would. Follow it in order:

- One step at a time. When the plan says to ask something, ask it and \
STOP. Wait for the contact's answer before continuing. Never answer for \
the contact and never speak their side of the conversation.
- DETAILS FOR THIS CALL lists the real values for this specific call — \
the contact's actual name and details. When the plan mentions a \
bracketed name like [contact_name], it means THE VALUE of that entry in \
the details. Always speak the value; never say the bracketed name, the \
brackets, or any [value: label] annotation aloud.
- Use the details naturally and confidently. Never ask the contact for \
information the details already contain, and NEVER invent, replace or \
"correct" a name or any other detail. If a value seems missing, address \
the contact with no name at all rather than a guess.
- Say the contact's name only where the plan calls for it — normally the \
opening and the close — never in every turn. The contact's name is \
ALWAYS its value in the details: if the name you hear on the call sounds \
different, that is a transcription error — keep using the details' \
value, and never switch to the version you heard.
- Use only the details the plan contains. If the contact asks something \
it does not answer, say someone from the team will follow up, and move \
on.
- Never repeat something you have already said in this call.
- Speak numbers, dates and times naturally, as words.
- Keep each turn short — one or two sentences. End the call politely \
once the plan is complete.

"""


def natural_greeting(content: AgentVersionContent) -> str:
    """The spoken opener: warm, brief, and it uses the contact's name.

    Spoken verbatim by TTS with the mustache resolved — the one place a
    fixed line earns its keep, because it guarantees the name is used at
    the top of the call (a live test showed the LLM asking who it was
    speaking to while holding the name in its notes). The heuristic
    picks the first person-name-like input field; everything after this
    line is the LLM phrasing the plan in its own words.
    """
    for f in variable_contract(content):
        key = f.key.lower()
        if "name" in key and not any(
            skip in key for skip in ("business", "company", "consultant", "agent", "brand")
        ):
            return "Hello, good day! Am I speaking with {{" + f.key + "}}?"
    return "Hello, good day!"


def render_call_plan(content: AgentVersionContent) -> str:
    """The LLM-facing plan: field references as bracketed names, [key].

    Deliberately NOT mustache: substituted values mid-prose arrive as
    "[value: label]" annotations the conversation model must parse
    inside its own instructions — observed live confusing it into
    reading annotations aloud and inventing a name. The plan refers to
    entries by name; the values live once each in the details block.
    """
    parts: list[str] = []
    for block in content.script_blocks:
        if isinstance(block, FieldRef):
            parts.append("[" + content.field_by_id(block.field_id).key + "]")
        else:
            parts.append(block.content)
    return "".join(parts)


def render_assistant_instructions(content: AgentVersionContent) -> str:
    """What a Telnyx assistant receives: frame, data, then direction.

    Data and direction are separated on purpose: the mustache
    placeholders appear exactly once each, in a fact table where the
    provider's substitution reads as data, and the plan references them
    by bracketed name. Still a pure render (FR-AGENT-3C) — the preamble
    is a constant, and nothing here is stored. Identical structure for
    scratch and run assistants, so the test path is the production path.
    """
    details = "".join(f"- {f.key}: {{{{{f.key}}}}}\n" for f in variable_contract(content))
    details_block = (
        "DETAILS FOR THIS CALL (real values for this specific call):\n" + details + "\n"
        if details
        else ""
    )
    return _ASSISTANT_PREAMBLE + details_block + "CALL PLAN:\n" + render_call_plan(content)


def dynamic_variable_keys(content: AgentVersionContent) -> tuple[str, ...]:
    """fields where kind = input -> dynamic_variables keys."""
    return tuple(f.key for f in variable_contract(content))


def insight_payloads(content: AgentVersionContent) -> list[dict[str, Any]]:
    """One Telnyx insight per output field — ours is a field, theirs is
    an insight. Schema shape per the live spike: single-property object,
    additionalProperties false; free-text fields carry no schema."""
    payloads: list[dict[str, Any]] = []
    for f in insight_schema(content):
        json_schema: dict[str, Any] | None = None
        if f.type == "enum":
            json_schema = {
                "type": "object",
                "properties": {f.key: {"type": "string", "enum": list(f.values)}},
                "required": [f.key],
                "additionalProperties": False,
            }
        payloads.append(
            {
                "field_id": f.id,
                "name": f.key,
                "instructions": f.instructions or f"Capture {f.key} from the conversation.",
                "json_schema": json_schema,
            }
        )
    return payloads


def insight_json_schema(content: AgentVersionContent) -> dict[str, Any]:
    """fields where kind = output -> the insight JSON schema."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for f in insight_schema(content):
        prop: dict[str, Any] = {"type": "string"}
        if f.type == "enum":
            prop["enum"] = list(f.values)
        if f.instructions:
            prop["description"] = f.instructions
        properties[f.key] = prop
        if f.required:
            required.append(f.key)
    # Telnyx rejects insight schemas without additionalProperties: false
    # (error 10015, observed live 6 Aug 2026).
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
