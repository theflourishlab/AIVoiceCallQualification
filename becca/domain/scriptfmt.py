"""Section-label spacing for call scripts — one source of truth.

Generated scripts label their sections two ways ("Role." / "OPENING:"),
and the FR-AGENT-4 prompt asks for a blank line between sections — which
the model does not reliably produce, and which field-chip boundaries
split across blocks. These regexes and the normaliser are shared by the
generation path (which bakes the spacing into the STORED script, so
every new agent is born consistent — user requirement, 14 Aug 2026) and
the review screen's display filter (which patches agents generated
before this existed).
"""

import re

# "Role." / "Opening." / "If they cannot make it." — a short label at the
# start of the text or after a newline, ending with a period.
SECTION_LABEL = re.compile(r"(^|\n)([A-Z][^.\n{}]{0,40}\.)(\s)")
# "OPENING:" / "CLOSE:" — all-caps colon labels from earlier generations,
# which arrived mid-text with no line breaks at all.
CAPS_LABEL = re.compile(r"\s*\b([A-Z][A-Z /&']{1,40}:)\s*")


def ensure_blank_lines(text: str, *, first_block: bool) -> str:
    """A blank line before every section label; never more than one.

    first_block strips the leading break only at the true start of the
    script — a label opening a LATER block keeps its gap (the bug that
    made Role→Opening run together when a field chip ended the previous
    block).
    """
    text = CAPS_LABEL.sub(r"\n\n\1 ", text)
    text = SECTION_LABEL.sub(
        lambda m: ("\n\n" if m.group(1) else "") + m.group(2) + m.group(3), text
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    if first_block:
        text = text.lstrip("\n")
    return text
