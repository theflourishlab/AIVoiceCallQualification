"""Score every stored transcript against the rubric.

    uv run python -m becca.evals --label baseline

Reads transcripts from the dev DB (both planes' worth — console session
spans clients), judges each with one Anthropic call, and writes
docs/evals/<label>.md + .json. Iteration loop: change one thing, place
the scripted batch of test calls, run this with a new label, compare.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from becca.config import load_settings
from becca.db.session import SessionFactory, make_engine
from becca.evals.harness import load_scoreable_calls, score_call
from becca.evals.html import write_index
from becca.evals.judge import AnthropicJudge
from becca.evals.report import write_batch


async def _run(
    label: str,
    out_dir: Path,
    judge_model: str | None,
    agent: str | None,
    since: datetime | None,
) -> int:
    settings = load_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set — the judge cannot run.", file=sys.stderr)
        return 1
    db = SessionFactory(make_engine())
    try:
        calls = await load_scoreable_calls(db, agent=agent, since=since)
    finally:
        await db.dispose()
    if not calls:
        print("No completed transcripts to score.", file=sys.stderr)
        return 1
    judge = (
        AnthropicJudge(settings.anthropic_api_key, model=judge_model)
        if judge_model
        else AnthropicJudge(settings.anthropic_api_key)
    )
    scorecards = []
    for call in calls:
        print(f"scoring {call.label} ({call.agent_name})...")
        scorecards.append(await score_call(call, judge))
    md_path, _ = write_batch(out_dir, label, scorecards, generated_at=datetime.now(UTC))
    index_path = write_index(out_dir)
    total = sum(len(s.violations) for s in scorecards)
    print(f"{len(scorecards)} calls scored, {total} violations -> {md_path}")
    print(f"review in the browser: {index_path.resolve()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default=f"{datetime.now(UTC):%Y-%m-%d}", help="batch label")
    parser.add_argument("--out", default="docs/evals", help="output directory")
    parser.add_argument("--judge-model", default=None, help="override the judge model")
    parser.add_argument("--agent", default=None, help="score only agents whose name contains this")
    parser.add_argument(
        "--since",
        default=None,
        help="score only calls placed at/after this ISO time (UTC unless an offset is given)",
    )
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since) if args.since else None
    return asyncio.run(_run(args.label, Path(args.out), args.judge_model, args.agent, since))


if __name__ == "__main__":
    sys.exit(main())
