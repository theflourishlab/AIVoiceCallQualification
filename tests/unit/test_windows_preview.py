from datetime import UTC, datetime, time

from becca.domain.model import AgentVersionContent, Field, FieldRef, TextBlock
from becca.domain.preview import speak_row
from becca.domain.windows import CallingWindow, TimeBand, is_open

WINDOW = CallingWindow(
    window=TimeBand(time(9, 0), time(18, 0)),
    days=frozenset({1, 2, 3, 4, 5, 6}),  # Monday to Saturday
    excluded_bands=(TimeBand(time(12, 0), time(14, 0)),),
    timezone="Africa/Lagos",
)


def test_open_inside_window() -> None:
    # 10:00 WAT on a Wednesday == 09:00 UTC
    assert is_open(WINDOW, datetime(2026, 8, 5, 9, 0, tzinfo=UTC))


def test_closed_on_sunday_and_at_night() -> None:
    assert not is_open(WINDOW, datetime(2026, 8, 9, 9, 0, tzinfo=UTC))  # Sunday
    assert not is_open(WINDOW, datetime(2026, 8, 5, 20, 0, tzinfo=UTC))  # 21:00 WAT


def test_closed_during_excluded_band() -> None:
    # 12:30 WAT == 11:30 UTC — the lunch skip
    assert not is_open(WINDOW, datetime(2026, 8, 5, 11, 30, tzinfo=UTC))


def _content() -> AgentVersionContent:
    return AgentVersionContent(
        fields=(Field(id=1, key="first_name", kind="input"),),
        script_blocks=(TextBlock("Good afternoon, is this "), FieldRef(1), TextBlock("?")),
    )


def test_speak_row_substitutes_the_contacts_value() -> None:
    assert (
        speak_row(_content(), {1: "Mr. Adewale Ogunbiyi"})
        == "Good afternoon, is this Mr. Adewale Ogunbiyi?"
    )


def test_speak_row_marks_missing_values_visibly() -> None:
    assert speak_row(_content(), {}) == "Good afternoon, is this ⟨missing first_name⟩?"
