# Becca — AI Voice Call Qualification

**Wema Hackaholics 7.0 · Team Becca · Yabatech**
*Powering Possibilities — Digital Transformation · Future of Work · Financial Inclusion*

Describe a phone call in plain English. Becca builds a voice agent, calls everyone on your list, and returns **one row of structured answers per person** — every answer linked to the exact moment in the transcript that proves it.

## Team Members

- **Flourish Olukotun**
- **Folaranmi Olaniyi**

---

## 🚀 Live Demo

* **Live Application:** **[https://app.becca.live](https://app.becca.live)** — where a business builds agents, imports contacts, tests, launches, and reads results.
* **Backend API:** Not separate — Becca is server-rendered, so the app above *is* the backend. One FastAPI deployment serves two hostnames: the client app, and the operator console at **[https://console.becca.live](https://console.becca.live)** (wallets, rates, margin, number inventory). Telnyx webhooks land at `/webhooks/telnyx`.
* **Recorded Demo:** *[Loom link — to be added]*

> **Access:** sign-in is Google OAuth with no self sign-up — a user exists only if staff added their email. That's the tenant boundary working as designed; we'll provision judge accounts on request.

---

## 🎯 The Problem

> **How might we let any organization — whether a bank with 40,000 customers or a growing business with 4,000 — call every single lead and get back structured, qualifiable answers, without the overhead of a human call center?**

Phone calls decide everything in Nigerian business: whether the loan is repaid, the appointment kept, the ticket upgraded, the lead ever real. And every organisation is sitting on a queue of calls it knows it should be making and isn't.

**Big organisations make the calls — at ruinous cost.** The only lever for volume is headcount: salaries, supervisors, QA, training, floor space, and attrition that resets the training bill every few months. Around the people sits a stack of per-seat subscriptions — dialer, CRM, recording, transcription, analytics — whose entire job is to reconstruct, after the fact, what the customer said. After all that spend, the "data" is a dropdown an agent clicked while the next call was ringing.

**Small businesses don't make the calls at all.** Nobody stands up a call centre for one campaign, and one person manages 40–60 dials a day — so 600 leads is two weeks of someone's life. The leads go cold instead. Not because the calls weren't worth making, but because the machine that makes them costs more than the answers are worth.

**Both fail in the same place.** The answer — the only thing anyone wanted — is treated as a by-product of the call, recovered afterwards from recordings and memory. And how many conversations you can have is capped by how many people you employ.

## ✨ Our Solution

**Becca replaces the call centre with a sentence.**

Write what the call should do. Becca builds the agent — what to ask, how to behave, which answers it must bring back. Upload your contact spreadsheet, map the columns, press launch. The agent holds a real conversation with every contact and returns a table: one row per person, one column per question, every value traceable to the words that produced it.

* **No hiring, no telephony contract, no code.** The brief is plain English; the agent is generated from it.
* **No seats, no subscriptions.** A prepaid wallet at a flat **$0.30/min**. 200 calls cost 200 calls; 20,000 calls is a bigger wallet balance, not a hiring round; a quiet month costs nothing.
* **The answer is the product of the call** — not an extraction performed later by three other vendors.

### For banks

The highest-volume, lowest-complexity conversations an institution has are exactly what a brief-driven agent does best:

- **Loan follow-up** — call stalled applicants; get back *still interested, amount, purpose, callback time* as a filterable table.
- **Repayment reminders** — capture intent to pay, promised date, and reason, each backed by the transcript line that matters when a promise is disputed.
- **KYC refresh** — confirm details across tens of thousands of customers at a per-minute cost, not a per-agent one.
- **Dormant account reactivation** — call the customers who went quiet and learn why.
- **Product outreach & post-complaint follow-up** — measure real interest, and whether the problem actually got fixed.

Each of these is today an outsourced per-seat campaign, or a thing quietly not done. On Becca, each is a brief, a spreadsheet and a wallet balance.

### Already in demand beyond banking

Becca is domain-agnostic — the agent is whatever the brief says — and the two people asking us hardest for it aren't banks:

- **A consultancy** that phones every enquiry to decide which deserve a partner's time. Today that's one person's entire day, getting less consistent as it runs. With Becca it's a sorted table.
- **A theatre producer** who wants to call ticket-holders about upgrading tiers — a revenue call only worth making when it costs cents, not a salaried hour.

The same goes for a hospital confirming appointments, a school chasing fees, an estate agency sorting 600 leads down to the 40 that are real.

### Call centre vs. Becca

| Call centre | Becca |
|---|---|
| Hire, train, supervise; attrition resets the ramp | Describe the call; test it in minutes |
| Fixed salaries + five per-seat tool licences | Flat per-minute rate from a prepaid wallet |
| More volume = more headcount | More volume = a bigger wallet balance |
| Script drift between agents | Every call runs the same frozen agent version |
| Outcome = a dropdown clicked between calls | One structured row per call, filterable, exportable |
| "She said she was interested" — unverifiable | Every value linked to the transcript that produced it |

### How it works

1. **Brief** — describe the call in plain English.
2. **Agent** — Becca generates the field set (inputs from your list, outputs from the conversation) and a call guide. Edits create immutable versions.
3. **Test calls** — real billed calls with stand-in values; tune until the answers come back right.
4. **Contacts** — upload a spreadsheet, map columns, numbers normalised to E.164 and de-duplicated; a preview reads three rows back exactly as the agent would speak them.
5. **Launch** — pre-flight checks, a one-time owner acknowledgement about the contacts, output schema freezes, and the agent dials each contact once, inside their local calling window and the run's spend cap. Pause, resume, or stop anytime.
6. **Results** — one insight per output field with provenance into the transcript; human corrections stored alongside, never over. "Qualified" is whatever saved view you define.
7. **Billing** — prepaid wallet, flat per-minute rate, append-only ledger as the source of truth.

### Built to be trusted with real money and real calls

- **Tenant isolation is structural** — Postgres row-level security on every tenant table from migration one; a forgotten `WHERE` clause can't leak one client into another.
- **Money is an append-only ledger** — the balance is a cache; in-flight calls hold their worst-case cost, so a run can never overspend.
- **No call dials twice** — idempotency keys guard dispatch and webhook redelivery; a crash can't double-call or double-charge.
- **Non-production can't dial strangers** — outside production the gateway refuses any number not on an explicit allowlist.
- **People are called at humane hours, with permission** — calling windows run in the contact's local time; no launch without the owner's acknowledgement.
- **Generation quality is measured** — an eval harness scores generated agents against a rubric, with versioned baselines in `docs/evals/`.

---

## 🛠️ Tech Stack

* **Frontend:** Server-rendered Jinja2 + HTMX; Alpine.js on the two screens needing client-side state. Hand-written design system, no framework.
* **Backend:** Python 3.14 + FastAPI — a web app and a dispatcher worker. Authlib Google OAuth, signed-cookie sessions, CSRF middleware.
* **Database:** PostgreSQL doing four jobs: data, job queue (`FOR UPDATE SKIP LOCKED`), pub/sub for the live monitor, and row-level security as the tenant boundary. SQLAlchemy 2.0 async + asyncpg; Alembic migrations.
* **Deployment:** Render (Frankfurt) — one service, two hostnames via `Host`-header routing. CI on GitHub Actions.
* **AI/APIs:** Anthropic (Claude) for agent generation via structured tool-use output; Telnyx for telephony with Ed25519-verified webhooks; `phonenumbers`, `openpyxl`, `tenacity`, Sentry.

Every stack decision is numbered `SD-nn` in `docs/techstack.md` with the alternative we rejected; every requirement is `FR-*` in `docs/beccavoicefrd.md`.

---

## ⚙️ How to Set Up and Run Locally

**Requirements:** Python 3.14, [uv](https://docs.astral.sh/uv/), Docker.

1. **Clone:**
   ```bash
   git clone https://github.com/Wema-Hackaholics-Hackathon/wema-hackaholics7-0-hackathon-yabatech-project-team-becca.git
   cd wema-hackaholics7-0-hackathon-yabatech-project-team-becca
   ```

2. **Start Postgres** (port 5433; also creates the non-superuser role RLS depends on):
   ```bash
   docker compose up -d
   ```

3. **Install dependencies:**
   ```bash
   uv sync --all-groups
   ```

4. **Configure** — the defaults never place a call:
   ```bash
   cp .env.example .env
   ```
   ```
   TELNYX_MODE=fake          # "real" only deliberately, with a human at the keyboard
   DATABASE_URL=postgresql+asyncpg://becca_app:becca@localhost:5433/becca
   ANTHROPIC_API_KEY=        # optional locally; a fake generator runs without it
   DIAL_ALLOWLIST=           # E.164 numbers this environment may dial. Empty = nobody
   ```

5. **Migrate:**
   ```bash
   uv run alembic upgrade head
   ```

6. **Run the web app** (`app.localtest.me` / `console.localtest.me` both resolve to 127.0.0.1):
   ```bash
   uv run uvicorn becca.web.app:create_app --factory --reload
   ```

7. **Run the worker** in a second terminal:
   ```bash
   uv run python -m becca.worker
   ```

> **Safety:** `TELNYX_MODE` defaults to `fake`, so nothing dials. Even in `real` mode, non-production refuses any number not on `DIAL_ALLOWLIST` — the guardrail lives in the gateway, not in config someone can forget.

**Checks** (the same gate CI runs):

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

---

## 📁 Repository Layout

```
becca/
  web/            the two planes — client_plane/, console_plane/, auth, sessions, webhooks
  worker/         dispatcher loop: claims queue rows, dials, reconciles, housekeeping
  domain/         pure domain logic (field sets, call guide formatting, dedupe, …)
  services/       use cases shared by web and worker
  generation/     brief → agent generation (Anthropic, with a fake for dev/tests)
  telnyx/         telephony gateway (HTTP client, with a fake for dev/tests)
  evals/          generation quality harness and scorecards
  db/             engine, sessions, RLS plumbing, models
alembic/          migrations
docs/             FRD, stack decisions, ADRs, product prototype, eval baselines, agent docs
scripts/          pg-init.sql (app role), staging wizard, spike script
tests/
CONTEXT.md        ubiquitous language / glossary — read this first
```

---

## 🤝 Contributing

Nobody commits to `main` directly. Work lands through a reviewed pull request with green CI.

1. **Start from an issue.** Every change traces to a GitHub issue; open one if it doesn't exist (`gh issue create`). Issues labelled `ready-for-human` / `ready-for-agent` are up for grabs — assign yourself. See `docs/agents/issue-tracker.md` and `docs/agents/triage-labels.md`.
2. **Branch off `main`:** `<type>/<issue>-<slug>` — e.g. `feat/42-spend-cap-advisory`, `fix/57-webhook-duplicate-debit`. One branch per issue, small enough to review in one sitting.
3. **Make the change.** Use `CONTEXT.md` vocabulary everywhere (the *Avoid* lists are real). Schema changes go through Alembic, and every tenant-scoped table gets `client_account_id` + an RLS policy — no exceptions. Anything touching dialling, billing or webhooks needs a test; the fakes exist so tests never reach Telnyx or Anthropic. Rejected a real alternative? Write an ADR in `docs/adr/`. Never dial a number outside your own `DIAL_ALLOWLIST` from a non-production environment.
4. **Run the checks** (command above) before pushing.
5. **Commit small**, imperative subject ≤ 72 chars, body explains *why*, `Closes #42` in the body.
6. **Open the PR:** `git push -u origin <branch>` then `gh pr create --fill`. The template asks what changed, how you tested it (including whether any real call was placed), and what you left out. Respond to review with new commits, not force-pushes. Approved PRs are **squash-merged** — the PR title and body become the commit message.

**Reviewers check:** does it do what the issue asked; could it leak one client into another; could it double-dial, double-charge, or dial off-allowlist; does the copy use the glossary's words; is there a test that would fail if it broke?

---

## 📚 Working on the project

- **Read before changing behaviour:** `CONTEXT.md`, `docs/beccavoicefrd.md`, `docs/techstack.md`, and `docs/open-conflicts.md` (known contradictions between the documents). `docs/agents/domain.md` explains how to use them.
- **Secrets** never go in `.env.example`. `.env` and `.env.staging` are gitignored and must stay so.
