"""The call-quality rubric (issue #1): mechanical checks are pure and
deterministic; the harness pairs a transcript with its own instruction
contract; scorecards render with evidence, not ratings."""

import asyncio
from datetime import UTC, datetime

from becca.domain.model import AgentVersionContent, Field, FieldRef, TextBlock
from becca.evals.harness import ScoreableCall, score_call, substituted_instructions
from becca.evals.mechanical import (
    check_ack_tic,
    check_annotation_leak,
    check_long_turn,
    check_verbatim_repeat,
)
from becca.evals.report import batch_to_json, batch_to_markdown
from becca.evals.rubric import CRITERIA, RUBRIC_VERSION, Violation

CONTENT = AgentVersionContent(
    fields=(
        Field(id=1, key="contact_name", kind="input"),
        Field(id=2, key="budget", kind="input"),
    ),
    script_blocks=(
        TextBlock("Confirm "),
        FieldRef(1),
        TextBlock(" is happy with the budget of "),
        FieldRef(2),
        TextBlock("."),
    ),
)


def _msg(role: str, text: str) -> dict:
    return {"role": role, "text": text}


# ---------------------------------------------------------- mechanical


def test_annotation_leak_catches_mustache_brackets_and_value_labels() -> None:
    messages = [
        _msg("assistant", "Am I speaking with {{contact_name}}?"),
        _msg("assistant", "Your budget of [budget] is noted."),
        _msg("assistant", "Hello [value: Bruce], good day."),
        _msg("assistant", "Plain honest sentence."),
        # A contact turn with markup is transcription noise, not conduct.
        _msg("user", "I said {{what}}?"),
    ]
    violations = check_annotation_leak(messages, frozenset({"contact_name", "budget"}))
    assert [v.turn for v in violations] == [0, 1, 2]


def test_annotation_leak_ignores_unknown_bracket_words() -> None:
    messages = [_msg("assistant", "We cover Lagos [and environs] too.")]
    assert check_annotation_leak(messages, frozenset({"budget"})) == []


def test_long_turn_flags_three_or_more_sentences() -> None:
    messages = [
        _msg("assistant", "One. Two."),
        _msg("assistant", "One. Two. Three."),
    ]
    violations = check_long_turn(messages)
    assert [v.turn for v in violations] == [1]
    assert "3 sentences" in violations[0].note


def test_ack_tic_flags_consecutive_identical_openers_only() -> None:
    messages = [
        _msg("assistant", "Got it, thanks Bruce."),
        _msg("user", "Sure."),
        _msg("assistant", "Got it, thanks. And the budget?"),
        _msg("assistant", "Understood. Anything else?"),
        _msg("assistant", "Got it. Bye."),  # not consecutive — no tic
    ]
    violations = check_ack_tic(messages)
    assert [v.turn for v in violations] == [2]


def test_verbatim_repeat_flags_repeated_full_sentences() -> None:
    messages = [
        _msg("assistant", "Someone from the team will follow up with you."),
        _msg("assistant", "Someone from the team will follow up with you!"),
        _msg("assistant", "Thanks. Bye."),  # short sentences never flagged
        _msg("assistant", "Thanks. Bye."),
    ]
    violations = check_verbatim_repeat(messages)
    assert [v.turn for v in violations] == [1]


# ------------------------------------------------------------- harness


def test_substituted_instructions_carry_values_and_fixed_greeting() -> None:
    rendered = substituted_instructions(CONTENT, {"contact_name": "Ada", "budget": "40m"})
    assert "FIXED GREETING" in rendered
    assert "Am I speaking with Ada?" in rendered
    assert "- budget: 40m" in rendered
    assert "{{" not in rendered  # every placeholder resolved
    assert "[contact_name]" in rendered  # the plan still refers by name


class _FakeJudge:
    async def score(self, *, instructions: str, messages: list[dict]):
        self.instructions = instructions
        return [Violation("invention", 1, "I am Bruce", "invented a name")], "Stiff."


def test_score_call_merges_mechanical_and_judged_in_turn_order() -> None:
    call = ScoreableCall(
        label="test-1",
        source="test_run",
        agent_name="Eko",
        content=CONTENT,
        values={"contact_name": "Ada", "budget": "40m"},
        messages=[
            _msg("assistant", "Hello {{contact_name}}!"),
            _msg("assistant", "I am Bruce"),
        ],
    )
    judge = _FakeJudge()
    card = asyncio.run(score_call(call, judge))
    assert [v.criterion for v in card.violations] == ["annotation_leak", "invention"]
    assert card.agent_turns == 2
    assert card.count("invention") == 1
    assert "Am I speaking with Ada?" in judge.instructions
    assert card.judge_overall == "Stiff."


# -------------------------------------------------------------- report


def _card() -> object:
    call = ScoreableCall(
        label="test-1",
        source="test_run",
        agent_name="Eko",
        content=CONTENT,
        values={},
        messages=[_msg("assistant", "Hello [budget].")],
    )

    class _Silent:
        async def score(self, *, instructions: str, messages: list[dict]):
            return [], ""

    return asyncio.run(score_call(call, _Silent()))


def test_batch_json_totals_and_version() -> None:
    data = batch_to_json("baseline", [_card()], generated_at=datetime.now(UTC))
    assert data["rubric_version"] == RUBRIC_VERSION
    assert data["totals"]["annotation_leak"] == 1
    assert {c["id"] for c in data["criteria"]} == {c.id for c in CRITERIA}
    assert data["calls"][0]["violations"][0]["criterion"] == "annotation_leak"


def test_batch_markdown_shows_evidence() -> None:
    md = batch_to_markdown("baseline", [_card()], generated_at=datetime.now(UTC))
    assert "| test-1 |" in md
    assert "**annotation_leak** (turn 0)" in md
    assert '"Hello [budget]."' in md


def test_write_index_embeds_every_batch_json(tmp_path) -> None:
    from becca.evals.html import write_index
    from becca.evals.report import write_batch

    write_batch(tmp_path, "r1", [_card()], generated_at=datetime.now(UTC))
    write_batch(tmp_path, "r2", [_card()], generated_at=datetime.now(UTC))
    html = write_index(tmp_path).read_text(encoding="utf-8")
    assert '"batch": "r1"' in html
    assert '"batch": "r2"' in html


def test_index_html_escapes_closing_script_tag_in_data() -> None:
    """A transcript quote containing </script> must not terminate the
    embedded data block early."""
    from becca.evals.html import index_html

    page = index_html([{"batch": "x</script><p>", "totals": {}, "calls": []}])
    data_zone = page.split('type="application/json">')[1].split("</script>")[0]
    assert "x<\\/script>" in data_zone
