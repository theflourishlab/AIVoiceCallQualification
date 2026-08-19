"""Scorecards on disk: one JSON (the data) + one Markdown (the read).

The JSON is the stable artifact — later presentation (the HTML view
across batches) renders from it, so its shape carries everything the
Markdown shows. Batches live side by side under docs/evals/, one pair
of files per batch label, committed so iterations stay diffable.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from becca.evals.rubric import CRITERIA, RUBRIC_VERSION, CallScorecard


def batch_to_json(
    label: str, scorecards: list[CallScorecard], *, generated_at: datetime
) -> dict[str, Any]:
    return {
        "batch": label,
        "rubric_version": RUBRIC_VERSION,
        "generated_at": generated_at.isoformat(),
        "criteria": [
            {"id": c.id, "title": c.title, "kind": c.kind, "definition": c.definition}
            for c in CRITERIA
        ],
        "totals": {c.id: sum(s.count(c.id) for s in scorecards) for c in CRITERIA},
        "calls": [
            {
                "label": s.call_label,
                "source": s.source,
                "agent": s.agent_name,
                "conversation_model": s.conversation_model,
                "turns": s.turns,
                "agent_turns": s.agent_turns,
                "counts": {c.id: s.count(c.id) for c in CRITERIA},
                "violations": [
                    {
                        "criterion": v.criterion,
                        "turn": v.turn,
                        "quote": v.quote,
                        "note": v.note,
                    }
                    for v in s.violations
                ],
                "judge_overall": s.judge_overall,
            }
            for s in scorecards
        ],
    }


def batch_to_markdown(
    label: str, scorecards: list[CallScorecard], *, generated_at: datetime
) -> str:
    lines = [
        f"# Call-quality scorecard — {label}",
        "",
        f"Rubric v{RUBRIC_VERSION} · {generated_at:%d %b %Y %H:%M} UTC · {len(scorecards)} calls",
        "",
        "## Violations per call",
        "",
        "| call | " + " | ".join(c.id for c in CRITERIA) + " | total |",
        "|" + "---|" * (len(CRITERIA) + 2),
    ]
    for s in scorecards:
        counts = [s.count(c.id) for c in CRITERIA]
        cells = " | ".join("·" if n == 0 else str(n) for n in counts)
        lines.append(f"| {s.call_label} | {cells} | {sum(counts)} |")
    totals = [sum(s.count(c.id) for s in scorecards) for c in CRITERIA]
    lines.append(
        "| **all** | "
        + " | ".join("·" if n == 0 else f"**{n}**" for n in totals)
        + f" | **{sum(totals)}** |"
    )
    for s in scorecards:
        lines += [
            "",
            f"## {s.call_label} — {s.agent_name}",
            "",
            f"{s.source} · {s.conversation_model} · {s.agent_turns} agent turns of {s.turns}",
            "",
        ]
        if s.judge_overall:
            lines += [f"> {s.judge_overall}", ""]
        if not s.violations:
            lines.append("No violations.")
        for v in s.violations:
            quote = " ".join(v.quote.split())
            if len(quote) > 240:
                quote = quote[:240] + "…"
            lines += [
                f"- **{v.criterion}** (turn {v.turn}): {v.note}",
                f'  - "{quote}"',
            ]
    lines.append("")
    return "\n".join(lines)


def write_batch(
    out_dir: Path, label: str, scorecards: list[CallScorecard], *, generated_at: datetime
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{label}.json"
    md_path = out_dir / f"{label}.md"
    json_path.write_text(
        json.dumps(batch_to_json(label, scorecards, generated_at=generated_at), indent=2),
        encoding="utf-8",
    )
    md_path.write_text(
        batch_to_markdown(label, scorecards, generated_at=generated_at), encoding="utf-8"
    )
    return md_path, json_path
