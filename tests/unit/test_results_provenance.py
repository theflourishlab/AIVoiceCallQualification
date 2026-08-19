"""Provenance and filter parsing (FR-RESULT-6/2), no DB."""

from becca.domain.model import Field
from becca.services.results import parse_filters, provenance

MESSAGES = [
    {
        "role": "assistant",
        "text": "Am I speaking with Chidinma?",
        "created_at": "2026-08-12T13:00:00Z",
    },
    {"role": "user", "text": "Yes, speaking.", "created_at": "2026-08-12T13:00:04Z"},
    {
        "role": "assistant",
        "text": "Will you still attend on the 22nd?",
        "created_at": "2026-08-12T13:00:10Z",
    },
    {
        "role": "user",
        "text": "Yes, I will attend, thank you.",
        "created_at": "2026-08-12T13:00:16Z",
    },
]


def test_provenance_quotes_the_contact_turn() -> None:
    quote, at = provenance(MESSAGES, "attend")
    assert quote == "Yes, I will attend, thank you."
    assert at == 16.0  # seconds into the call


def test_agent_saying_the_value_is_not_provenance() -> None:
    """The agent asked about "the 22nd"; the contact never said it."""
    quote, at = provenance(MESSAGES, "the 22nd")
    assert quote is None and at is None


def test_unspoken_value_gets_no_fabricated_quote() -> None:
    assert provenance(MESSAGES, "maybe next week") == (None, None)
    assert provenance(MESSAGES, "") == (None, None)
    assert provenance([], "yes") == (None, None)


def test_unparseable_timestamps_still_yield_the_quote() -> None:
    messages = [{"role": "user", "text": "Yes I will attend", "created_at": "0:04"}]
    quote, at = provenance(messages, "attend")
    assert quote == "Yes I will attend"
    assert at is None


def test_parse_filters_keeps_only_schema_fields() -> None:
    outputs = [Field(id=3, key="still_attending", kind="output", type="enum", values=("yes", "no"))]
    filters = parse_filters(
        {"f_3": "yes", "f_99": "nope", "f_x": "junk", "other": "1", "f_3x": "bad"}, outputs
    )
    assert filters == {3: "yes"}
