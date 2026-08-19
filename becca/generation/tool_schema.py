"""The generation tool: ONE object, never three that must agree (FR-AGENT-4).

The model emits the field set and the script blocks together. It cannot
emit a script that names a variable, because script blocks reference
fields by id — the incoherent output is unrepresentable in the schema.
"""

from typing import Any

EMIT_AGENT_TOOL: dict[str, Any] = {
    "name": "emit_agent",
    "description": (
        "Emit the complete voice agent: its field set and its call script, "
        "as one object. Input fields are values the contact list must "
        "supply; output fields are captured from the conversation. The "
        "script is a sequence of blocks; a field_ref block points at a "
        "field by its id and is spoken as that field's value."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "agent_name": {"type": "string", "description": "Short display name for the agent"},
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "Unique within this agent"},
                        "key": {
                            "type": "string",
                            "description": "snake_case name, unique across BOTH kinds",
                        },
                        "kind": {"type": "string", "enum": ["input", "output"]},
                        "required": {"type": "boolean"},
                        "type": {"type": "string", "enum": ["text", "enum"]},
                        "values": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Allowed values when type is enum",
                        },
                        "instructions": {
                            "type": "string",
                            "description": "For output fields: what exactly to capture",
                        },
                    },
                    "required": ["id", "key", "kind", "required", "type"],
                    "additionalProperties": False,
                },
            },
            "script_blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["text", "field_ref"]},
                        "content": {"type": "string"},
                        "field_id": {"type": "integer"},
                    },
                    "required": ["type"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["agent_name", "fields", "script_blocks"],
        "additionalProperties": False,
    },
}

REGENERATE_PROMPT = """\
You are fixing an existing outbound voice-call agent based on what the
user heard on a test call. You receive the current agent (field set +
script blocks as JSON) and a plain-language description of the problem.

Emit the corrected agent via the emit_agent tool. Rules:
- Scope the change to the described problem. Do not rewrite unaffected
  parts of the script.
- The script is behavioural direction, not verbatim dialogue — keep it
  that way (and if the current script contains lines to recite, you may
  convert only the affected part to behavioural direction).
- PRESERVE the field set — same ids, keys, kinds — unless the described
  problem cannot be fixed without changing it.
- Keep field ids stable for every field you keep: column mappings and
  results attribution point at them.
- The script's Opening must still have the agent identify the business
  and state the call's purpose.
"""

SYSTEM_PROMPT = """\
You design outbound voice-call agents for Nigerian small businesses.
Given a plain-English brief, emit one agent via the emit_agent tool.

The script is BEHAVIOURAL DIRECTION, not lines to recite. Write it as
short labelled sections addressed to the agent — what to do, in what
order, and where the hard limits are — so the agent phrases everything
naturally in its own words on every call. Never write verbatim dialogue
for the agent to repeat.

Structure the script as these sections, in this order, adapting to the
brief. Begin each section with its label (e.g. "Role.") and separate
sections with a BLANK LINE (a double newline in the text blocks) so the
script reads as short paragraphs:
- Role. Who the agent is calling on behalf of, and its manner (warm,
  brief, never pushy). Keep the whole call under two minutes.
- Opening. Greet the contact by name, identify the business, and state
  the call's purpose in one sentence — Nigerian consumer rules require
  identity and purpose before anything else. Confirm they are the right
  person before continuing.
- One section per goal of the call (confirmations, questions to ask,
  reminders to give). Each question the agent must ask maps to an
  output field. Tell the agent to ask, then listen.
- Boundaries. What the agent must never do: never invent details, never
  quote figures the script does not contain; if asked something it
  cannot answer, say someone from the team will follow up.
- Close. Thank them by name and end warmly.

Rules:
- Reference every per-contact value (names, dates, times, amounts) as an
  input field via a field_ref block — never write a placeholder or a
  literal example value into script text.
- Every data point the brief wants captured becomes an output field.
  Prefer enum over free text wherever the brief implies a closed set;
  enum values are snake_case.
- Output fields never appear in the script; they are extracted after the
  call.
- Field keys are snake_case and unique across both kinds.
- Every field_ref's field_id must exist in fields.
"""
