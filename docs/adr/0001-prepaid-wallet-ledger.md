# Prepaid wallet with an append-only ledger, flat per-minute billing

**Date** 2026-08-14 · **Status** accepted

Becca replaced post-paid cost×margin invoicing (FRD §12, FR-BILL-1..9) with a prepaid
per-client wallet debited at a flat per-minute rate (default $0.30/min, round-up,
test calls included). Chosen for the cash-flow inversion (the client pays first;
Becca stops fronting Telnyx cost and carrying collection risk), price legibility
(one public number, so minute counts no longer hide anything), and prepaid being the
native Nigerian model. Becca deliberately absorbs the $35/no./mo number MRC —
clients pay per-minute and nothing else — accepting that a dormant client with a
number is a ~$35/mo loss.

The load-bearing choices, each with a real alternative rejected:

- **Append-only `wallet_ledger` is the source of truth; the balance column is a
  transactional cache.** A bare balance column can't explain itself and corrupts
  silently under concurrency. Enforced by a **raising trigger, not audit_log's
  silent `DO INSTEAD NOTHING` rules** — Postgres refuses `ON CONFLICT` (which
  settlement's idempotency requires) on a table with rules, and a loud failure is
  the right posture for money anyway (migration 0011).
- **Reservations are computed from in-flight status, never stored.** Every attempt
  in `'dialling'` holds rate × 15 min (the reconciliation timeout, so a hold is
  provably sufficient). Stored reservation rows would need compensating writes on
  all three leak paths (`AmbiguousDialError`, stuck-dialling reconcile, 403 refund)
  and can drift; a computed hold dies with the row's status for free.
- **Ledger rows key on `call.id`/`test_run.id`, never `call.idempotency_key`** —
  the 403 refund path deletes a dialling call row and reuses its key; a uuid never
  returns. The FK on `call_id` doubles as a tripwire against deleting settled calls.
- **The rate is snapshotted at claim time** so no call bills at a rate not in force
  when it dialled; staff rate changes notify the client (never silent repricing).
- **Settlement debits actuals and may go negative** — clamping would be a silent
  gift and a lie in the ledger; a negative balance just blocks all new dialling.
- **FR-BILL-8 is inverted on purpose**: insufficient balance pauses runs. Cost sync
  survives as Becca's internal margin monitor; pre-wallet invoices are frozen
  receipts.

Full requirement text: FRD Amendment A1 (§12-W). Vocabulary: CONTEXT.md § Billing.
