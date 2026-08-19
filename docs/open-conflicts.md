# Open conflicts between source documents

Contradictions between `beccavoicefrd.md` and `techstack.md` that are **not yet resolved**. These are deliberately not ADRs — an ADR records a decision, and none has been made here.

Anyone acting on either document should check this file first. When a conflict is settled, delete its entry and record the outcome wherever it belongs — an ADR if it clears that bar, otherwise a correction to the source document.

---

## 1. Is there a periodic scheduler?

**Status:** unresolved
**Decide before:** Phase 4 (Dispatch), when the worker loop is built

**`techstack.md` SD-22 says no.** "No library, no cron, no timer at all… no APScheduler, no platform cron, no `pg_cron`." Housekeeping — the orphan sweeper, cost sync, balance checks — runs as a counter inside the worker's existing poll loop. The doc explicitly records this as a reversal: "My original answer was APScheduler. The answer is now: nothing." Its listed trade-off is that housekeeping stops when the worker is down, judged acceptable because dialling stops too and that is far more visible.

**`beccavoicefrd.md` FR-BUILD-3 says yes.** "The periodic scheduler still exists for the orphan sweeper, digests, invoicing and cost sync (see stack decisions), but nothing in the recording path needs it."

**Why it matters.** Read one way, someone builds a scheduler that SD-22 argues shouldn't exist — and a second scheduled process reopens the concurrency question SD-09 closed by moving the governor into the database. Read the other way, housekeeping is assumed to be handled by something that was never built.

**Two signals that FR-BUILD-3 is the stale line.** It cites *digests* among the scheduler's jobs, but FR-NOTIFY-2B removed the daily 18:00 summary along with all email. And SD-22 documents its own reversal explicitly, which reads as the later thought.

**Working assumption until settled:** SD-22 — no scheduler. Nothing has been built either way, so this is cheap to reverse.
