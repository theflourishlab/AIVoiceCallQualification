"""Section spacing is a stored property of generated scripts (user
requirement, 14 Aug 2026), not a display accident — and block boundaries
(field chips) must not eat the blank line between sections."""

from becca.domain.model import AgentVersionContent, Field, FieldRef, TextBlock
from becca.domain.scriptfmt import ensure_blank_lines
from becca.generation.generate import _spaced


def test_caps_labels_get_blank_lines_mid_text() -> None:
    out = ensure_blank_lines("ROLE: Be warm. OPENING: Greet them kindly.", first_block=True)
    assert out.startswith("ROLE:")  # the very first label has no gap above
    assert "\n\nOPENING:" in out  # every later label gets one


def test_sentence_labels_get_blank_lines() -> None:
    out = ensure_blank_lines("Role. Be warm.\nOpening. Greet them.", first_block=True)
    assert "\n\nOpening." in out


def test_label_starting_a_later_block_keeps_its_gap() -> None:
    # The bug that ran Role→Opening together: a field chip ends the
    # previous block, so the next section starts a NEW block — whose
    # leading break must survive.
    out = ensure_blank_lines("SELLING INTEREST: Ask about selling.", first_block=False)
    assert out.startswith("\n\nSELLING INTEREST:")


def test_never_more_than_one_blank_line() -> None:
    out = ensure_blank_lines("Role. Hi.\n\n\n\nClose. Bye.", first_block=True)
    assert "\n\n\n" not in out


def test_generated_content_is_spaced_across_chip_boundaries() -> None:
    content = AgentVersionContent(
        fields=(Field(id=1, key="contact_name", kind="input"),),
        script_blocks=(
            TextBlock("ROLE: Be warm. OPENING: Greet "),
            FieldRef(1),
            TextBlock(" politely. SELLING INTEREST: Ask about selling."),
        ),
    )
    spaced = _spaced(content)
    b0, b1, b2 = spaced.script_blocks
    assert isinstance(b1, FieldRef)  # refs pass through untouched
    assert b0.content.startswith("ROLE:") and "\n\nOPENING:" in b0.content
    assert "\n\nSELLING INTEREST:" in b2.content
