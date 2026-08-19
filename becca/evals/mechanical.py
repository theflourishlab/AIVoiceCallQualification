"""The four criteria code can check without judgment.

Pure functions over a transcript's message list (the shape stored by
results/testing: dicts with at least "role" and "text"). Turn indices in
violations refer to positions in that list, so evidence lines up with
what the judge and the report see.
"""

import re
from typing import Any

from becca.evals.rubric import Violation

_MUSTACHE = re.compile(r"\{\{.*?\}\}")
_VALUE_ANNOTATION = re.compile(r"\[[^\[\]]+:\s*[^\[\]]+\]")
_BRACKET_TOKEN = re.compile(r"\[([a-z][a-z0-9_]*)\]")
# Naive on purpose: transcripts are spoken text, not prose with
# abbreviations. Splits on sentence-enders followed by space or end.
_SENTENCE_END = re.compile(r"[.!?]+(?:\s+|$)")


def _agent_turns(messages: list[dict[str, Any]]) -> list[tuple[int, str]]:
    return [
        (i, str(m.get("text") or ""))
        for i, m in enumerate(messages)
        if m.get("role") == "assistant" and str(m.get("text") or "").strip()
    ]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def check_annotation_leak(
    messages: list[dict[str, Any]], field_keys: frozenset[str]
) -> list[Violation]:
    out: list[Violation] = []
    for i, text in _agent_turns(messages):
        hits = _MUSTACHE.findall(text) + _VALUE_ANNOTATION.findall(text)
        hits += [m.group(0) for m in _BRACKET_TOKEN.finditer(text) if m.group(1) in field_keys]
        for hit in hits:
            out.append(Violation("annotation_leak", i, text, f"spoke markup aloud: {hit}"))
    return out


def check_long_turn(messages: list[dict[str, Any]]) -> list[Violation]:
    out: list[Violation] = []
    for i, text in _agent_turns(messages):
        n = len(_sentences(text))
        if n > 2:
            out.append(Violation("long_turn", i, text, f"{n} sentences in one turn"))
    return out


def _opener(text: str) -> str:
    words = re.sub(r"[^a-z' ]", " ", text.lower()).split()
    return " ".join(words[:2])


def check_ack_tic(messages: list[dict[str, Any]]) -> list[Violation]:
    out: list[Violation] = []
    previous: str | None = None
    for i, text in _agent_turns(messages):
        opener = _opener(text)
        if opener and opener == previous:
            out.append(
                Violation("ack_tic", i, text, f'opened "{opener}" same as the previous turn')
            )
        previous = opener
    return out


def check_verbatim_repeat(messages: list[dict[str, Any]]) -> list[Violation]:
    out: list[Violation] = []
    seen: set[str] = set()
    for i, text in _agent_turns(messages):
        for sentence in _sentences(text):
            normalised = re.sub(r"[^a-z0-9 ]", "", sentence.lower()).strip()
            if len(normalised.split()) < 5:
                continue
            if normalised in seen:
                out.append(
                    Violation("verbatim_repeat", i, text, f'repeated sentence: "{sentence}"')
                )
            else:
                seen.add(normalised)
    return out


def run_mechanical_checks(
    messages: list[dict[str, Any]], field_keys: frozenset[str]
) -> list[Violation]:
    return (
        check_annotation_leak(messages, field_keys)
        + check_long_turn(messages)
        + check_ack_tic(messages)
        + check_verbatim_repeat(messages)
    )
