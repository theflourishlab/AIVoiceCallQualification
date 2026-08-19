"""The behavioural frame, natural greeting and boundary pruning —
regression tests for the live role-inversion call (11 Aug 2026) and the
behavioural-script decision (12 Aug 2026)."""

from becca.domain.codec import content_from_json
from becca.domain.model import AgentVersionContent, Field, FieldRef, TextBlock
from becca.domain.serialize import natural_greeting, render_assistant_instructions

CONTENT = AgentVersionContent(
    fields=(
        Field(id=1, key="business_name", kind="input"),
        Field(id=2, key="contact_name", kind="input"),
    ),
    script_blocks=(
        TextBlock("Opening. Greet "),
        FieldRef(2),
        TextBlock(" by name, identify "),
        FieldRef(1),
        TextBlock(" and state the purpose."),
    ),
)


def test_assistant_instructions_separate_data_from_direction() -> None:
    """The structural fix for the invented-name call: mustache appears
    exactly once per input field, in the details table; the plan refers
    to entries by bracketed name and contains no substitutions."""
    instructions = render_assistant_instructions(CONTENT)
    assert "You are the caller" in instructions
    assert "Wait for the contact's answer" in instructions
    details, plan = instructions.split("CALL PLAN:")
    assert "- business_name: {{business_name}}" in details
    assert "- contact_name: {{contact_name}}" in details
    assert "{{" not in plan  # no substitution targets mid-prose
    assert "Greet [contact_name] by name, identify [business_name]" in plan
    assert instructions.count("{{contact_name}}") == 1


def test_preamble_pins_name_usage_to_plan_and_details() -> None:
    """The round-3 rule (13 Aug 2026): Haiku said the contact's name in
    nearly every turn and adopted the ASR's mishear of it ("Floris",
    "Flurry") over the DETAILS value. Agent-agnostic on purpose — it
    leans only on sections every generated script has."""
    instructions = render_assistant_instructions(CONTENT)
    assert "only where the plan calls for it" in instructions
    assert "never switch to the version you heard" in instructions


def test_greeting_asks_for_the_contact_by_name() -> None:
    """The opener is the one fixed line: it must use the person's name
    (live test: the LLM held the name in its notes and still asked who
    it was speaking to). Picks the person-name-like input field."""
    assert natural_greeting(CONTENT) == "Hello, good day! Am I speaking with {{contact_name}}?"


def test_greeting_skips_business_like_names_and_falls_back() -> None:
    content = AgentVersionContent(
        fields=(Field(id=1, key="business_name", kind="input"),),
        script_blocks=(FieldRef(1),),
    )
    assert natural_greeting(content) == "Hello, good day!"


def test_unreferenced_input_fields_are_dropped_at_the_boundary() -> None:
    """FR-AGENT-3B: an input exists because the script uses it. The live
    generator once emitted a phantom ticket_amount input."""
    content = content_from_json(
        {
            "fields": [
                {"id": 1, "key": "contact_name", "kind": "input", "required": True, "type": "text"},
                {
                    "id": 9,
                    "key": "ticket_amount",
                    "kind": "input",
                    "required": True,
                    "type": "text",
                },
                {
                    "id": 3,
                    "key": "will_attend",
                    "kind": "output",
                    "required": True,
                    "type": "enum",
                    "values": ["yes", "no"],
                },
            ],
            "script_blocks": [
                {"type": "text", "content": "Hello "},
                {"type": "field_ref", "field_id": 1},
            ],
        }
    )
    keys = [f.key for f in content.fields]
    assert "ticket_amount" not in keys  # phantom input pruned
    assert "will_attend" in keys  # outputs never require references
