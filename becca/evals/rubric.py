"""The call-quality rubric (GitHub issue #1, strand 1).

A score is a list of VIOLATIONS WITH EVIDENCE, never a 1-5 rating: each
violation quotes the offending turn, so a scorecard is a set of facts
about one transcript measured against that call's own instruction sheet
(preamble rules + DETAILS + CALL PLAN). Two scorecards are comparable
only when their rubric_version matches — bump RUBRIC_VERSION whenever a
criterion is added, removed, or redefined.
"""

from dataclasses import dataclass
from typing import Literal

RUBRIC_VERSION = 1

CriterionKind = Literal["mechanical", "judged"]


@dataclass(frozen=True)
class Criterion:
    id: str
    title: str
    kind: CriterionKind
    # For judged criteria this text IS the judge's working definition —
    # edit it here and the judge prompt follows.
    definition: str


CRITERIA: tuple[Criterion, ...] = (
    # -------- mechanical: deterministic code, no judgment ------------
    Criterion(
        "annotation_leak",
        "Annotation leakage",
        "mechanical",
        "The agent spoke markup aloud: mustache ({{...}}), a bracketed"
        " field reference ([key]), or a [value: label] annotation.",
    ),
    Criterion(
        "long_turn",
        "Turn length",
        "mechanical",
        "An agent turn ran past the 'one or two sentences' rule (flagged at three or more).",
    ),
    Criterion(
        "ack_tic",
        "Acknowledgement tic",
        "mechanical",
        "Consecutive agent turns opened with the same acknowledgement"
        " ('Got it, thanks X' every turn).",
    ),
    Criterion(
        "verbatim_repeat",
        "Verbatim repetition",
        "mechanical",
        "The agent said the same sentence twice in one call.",
    ),
    # -------- judged: needs reading comprehension --------------------
    Criterion(
        "redundant_question",
        "Redundant question",
        "judged",
        "The agent asked the contact for information the DETAILS FOR"
        " THIS CALL block already contained (e.g. asking for a name or"
        " requirement that was provided as a value).",
    ),
    Criterion(
        "invention",
        "Invention",
        "judged",
        "The agent stated a name, fact, or detail that appears nowhere"
        " in its instructions and nowhere in the contact's own turns —"
        " invented, replaced, or 'corrected' a provided value.",
    ),
    Criterion(
        "context_loss",
        "Re-asking / context loss",
        "judged",
        "The agent re-asked a question the contact had already answered"
        " in this call, or lost a correction the contact made about"
        " themselves (e.g. buy vs rent).",
    ),
    Criterion(
        "mishear_adoption",
        "Mishear adoption",
        "judged",
        "The transcript shows an obvious transcription mishear and the"
        " agent ADOPTED it as fact (repeated the garbled name/word back,"
        " built on it) instead of gliding past or clarifying. The"
        " mishear itself is not the violation; adopting it is.",
    ),
    Criterion(
        "plan_deviation",
        "Plan adherence",
        "judged",
        "The agent skipped a plan step, took steps out of order, packed"
        " several asks into one turn, answered for the contact, or"
        " improvised beyond the plan instead of deflecting off-plan"
        " questions to a follow-up from the team.",
    ),
    Criterion(
        "closing_naturalness",
        "Closing & naturalness",
        "judged",
        "The close was abrupt, cold, or missing once the plan was"
        " complete; or delivery was unnatural — numbers/dates read as"
        " digits, stiff recited phrasing, robotic register.",
    ),
)

CRITERIA_BY_ID = {c.id: c for c in CRITERIA}
JUDGED_CRITERIA = tuple(c for c in CRITERIA if c.kind == "judged")
MECHANICAL_CRITERIA = tuple(c for c in CRITERIA if c.kind == "mechanical")


@dataclass(frozen=True)
class Violation:
    criterion: str  # Criterion.id
    turn: int  # index into the transcript's message list
    quote: str  # the offending text, verbatim from the transcript
    note: str  # one line: why this is a violation


@dataclass(frozen=True)
class CallScorecard:
    call_label: str  # human-readable: "test-3" or the call id
    source: str  # "test_run" | "call"
    agent_name: str
    conversation_model: str  # what the assistant ran on, if known
    turns: int  # total messages
    agent_turns: int
    violations: tuple[Violation, ...]
    judge_overall: str  # judge's one-paragraph impression

    def count(self, criterion_id: str) -> int:
        return sum(1 for v in self.violations if v.criterion == criterion_id)
