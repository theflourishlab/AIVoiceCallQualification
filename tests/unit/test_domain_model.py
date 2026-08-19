import pytest

from becca.domain.model import (
    AgentVersionContent,
    DanglingFieldRef,
    DuplicateFieldKey,
    Field,
    FieldRef,
    TextBlock,
)
from becca.domain.serialize import (
    dynamic_variable_keys,
    insight_json_schema,
    render_instructions,
)
from becca.domain.views import insight_schema, variable_contract

# The FRD §4.1 example: two inputs, one output, a script referencing both inputs.
FIELDS = (
    Field(id=1, key="first_name", kind="input"),
    Field(id=2, key="visit_date", kind="input"),
    Field(
        id=3,
        key="budget_band",
        kind="output",
        type="enum",
        values=("under_50m", "50_80m", "80_120m"),
    ),
)
BLOCKS = (
    TextBlock("Good afternoon, is this "),
    FieldRef(1),
    TextBlock("? You are booked for "),
    FieldRef(2),
)


def content() -> AgentVersionContent:
    return AgentVersionContent(fields=FIELDS, script_blocks=BLOCKS)


def test_views_are_projections_of_one_list() -> None:
    c = content()
    assert [f.key for f in variable_contract(c)] == ["first_name", "visit_date"]
    assert [f.key for f in insight_schema(c)] == ["budget_band"]


def test_render_instructions_writes_mustache_only_at_serialisation() -> None:
    assert (
        render_instructions(content())
        == "Good afternoon, is this {{first_name}}? You are booked for {{visit_date}}"
    )


def test_rename_writes_one_value_and_everything_follows() -> None:
    """FR-AGENT-6: renaming touches fields[].key; script and views follow."""
    renamed = content().rename_field(1, "given_name")
    assert renamed.script_blocks == BLOCKS  # untouched — refs are by id
    assert render_instructions(renamed).startswith("Good afternoon, is this {{given_name}}")
    assert dynamic_variable_keys(renamed) == ("given_name", "visit_date")


def test_dangling_field_ref_is_unrepresentable() -> None:
    """FR-AGENT-3A: the only validation, at the only boundary."""
    with pytest.raises(DanglingFieldRef) as exc:
        AgentVersionContent(fields=FIELDS, script_blocks=(*BLOCKS, FieldRef(99)))
    assert exc.value.field_ids == frozenset({99})


def test_duplicate_key_is_unrepresentable() -> None:
    """A name can never be both an input and an output (FR-AGENT-3)."""
    with pytest.raises(DuplicateFieldKey):
        AgentVersionContent(
            fields=(
                Field(id=1, key="budget", kind="input"),
                Field(id=2, key="budget", kind="output"),
            )
        )


def test_insight_json_schema_from_output_fields() -> None:
    schema = insight_json_schema(content())
    assert schema["properties"]["budget_band"]["enum"] == ["under_50m", "50_80m", "80_120m"]
    assert schema["required"] == ["budget_band"]
