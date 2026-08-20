# Becca — AI Voice Call Qualification

**Wema Hackaholics 7.0 · Team Becca · Yabatech**
*Theme: Powering Possibilities — Digital Transformation, Future of Work, Financial Inclusion*

Becca lets a business describe a phone call in plain English, then builds a voice agent that places that call to an imported contact list and brings back structured answers — one row of results per person called, with every extracted value linked back to the moment in the transcript that produced it.

## Team Members

- **Flourish Olukotun**
- **Folaranmi Olaniyi**

---

## 🚀 Live Demo

* **Live Application:** **[https://app.becca.live](https://app.becca.live)** — the client app, where a business builds agents, imports contacts, runs test calls, launches, and reads results.
* **Backend API:** Not a separate service. Becca is server-rendered, so the application above *is* the backend — one FastAPI deployment answering on two hostnames. The agency-facing plane is **[https://console.becca.live](https://console.becca.live)** (Becca staff only), and the Telnyx webhook endpoint answers at `/webhooks/telnyx` on either hostname.
* **Recorded Demo:** *[Loom link — to be added]*

> **Access:** sign-in is Google OAuth and there is no self sign-up — a user exists only if staff added their email, which is the tenant boundary working as designed. For judging, we will provision an account on request, and the recorded demo walks the full flow end to end.

| Plane | URL | Who uses it |
|---|---|---|
| **Client app** | [https://app.becca.live](https://app.becca.live) | Client businesses — build agents, import contacts, run test calls, launch, read results, see what they owe |
| **Becca console** | [https://console.becca.live](https://console.becca.live) | Becca staff only — every client account, wallets and top-ups, per-minute rates, the margin monitor, number inventory, channel allocation |

Both hostnames point at the **same deployment**; FastAPI routes on the `Host` header to one of two separate applications that share a database.

---

## 🎯 The Problem

> **How might we let any organisation — a bank with 40,000 customers to reach this month, or a business with 400 — hold every one of those conversations and get back structured, verifiable answers, without hiring a floor of agents or stitching together five monthly subscriptions to find out what was said?**

Nigerian businesses run on phone calls. Confirming, qualifying, reminding, collecting, verifying — the work that decides whether a loan is repaid, a seat is upgraded, an appointment is kept, or a lead was ever real happens on the phone.

A lender needs to know which of 800 applicants still wants the loan. A consultancy needs to work out which of 500 enquiries deserve a partner's time. A theatre producer needs to ask 2,000 ticket-holders whether they will upgrade tier before opening night. A hospital needs to confirm which of tomorrow's 120 appointments will be kept. A school needs to reach 400 parents about fees. An estate agency has 600 leads from a campaign and needs to know which 40 are real.

Almost every institution is carrying a queue of calls it knows it should be making and isn't.

### At enterprise scale, the calls get made — at ruinous cost

A bank can run the numbers on any given quarter. Eight thousand loan applications that stalled halfway. Forty thousand customers whose records are due a KYC refresh. A default book that needs contact every week. A dormant-account list nobody has called in a year.

The only lever for that volume is headcount, and headcount is a cost that never scales down: salaries, supervisors, QA analysts, training, floor space, headsets, and attrition that resets the training bill every few months. Adding a campaign means adding agents — or bumping something else off the queue. Outsourcing to a BPO only swaps the shape of the bill: a contract, a seat minimum and a monthly commitment.

Then there is the tooling wrapped around them. A dialer. A CRM. Call recording. A transcription vendor. A speech-analytics or QA tool. A BI seat to read the result. Five or six per-seat monthly subscriptions, integrated at further expense, assembled for a single purpose: **to work out, after the fact, what the customer actually said.** And after all of it, the outcome data is still whatever an agent picked from a disposition dropdown while the next call was already ringing — inconsistent between agents, unverifiable, and thin.

### At smaller scale, the calls simply never happen

A business with 600 leads from a campaign cannot justify a call centre for one campaign, and putting a staff member on the phone costs a fortnight of their life at 40–60 dials a day. So the leads go cold, the appointments get missed, the fees go unpaid — not because the calls weren't worth making, but because the only machine that makes them costs more than the answers are worth.

### Both ends fail in the same place

Whatever the scale, the output is the same mess. Answers land in a disposition dropdown, a notebook, a WhatsApp message, or a spreadsheet somebody filled in from memory at 6pm. There is nothing to filter on, no proof of what was actually said, and no way to answer *"which of these people said yes and can pay this month?"* without re-reading everything.

**The structured answer — the only part anybody actually wanted — is treated as a by-product of the call instead of its output.** Every expensive thing in the stack exists to recover it afterwards: the recording, the transcription, the QA sample, the analyst tagging outcomes. Meanwhile the one number that governs how many conversations an organisation can have is how many people it employs.

## ✨ Our Solution

**Becca replaces the call centre with a sentence.**

Someone writes, in plain English, what the call should do. Becca reads that **brief** and builds a voice agent — what to ask, how to behave, and crucially which pieces of information it must bring back. They upload the contact spreadsheet, map the columns, and press launch. The agent dials every contact, holds a real conversation in real time, and returns **one row per person with one column per question** — and every extracted answer links back to the moment in the transcript where the person said it.

No hiring. No call centre. No telephony account. No prompt engineering, no flow-chart builder, no code.

And the structured answer is the product of the call, not an extraction performed on it afterwards by three other vendors. There is no dialer to license, no transcription subscription, no QA tool, no analyst tagging dispositions — the row arrives already filterable.

There are no seats, either. Each account holds a prepaid **wallet** and pays a flat per-minute rate (default **$0.30/min**, test calls included), so a 200-contact campaign costs what 200 calls cost, a 20,000-contact campaign is a larger wallet balance rather than a hiring round, and a quiet month costs nothing at all.

### What this means for a bank

A voice agent that takes its instruction in plain English and returns a validated row is a direct fit for the highest-volume, lowest-complexity conversations an institution has — precisely the ones that consume a call centre's capacity and reward it least:

- **Loan origination follow-up.** Call the applicants who stalled mid-application. Capture, per person: still interested, amount wanted, purpose, employment status, best time to call back. What comes back is a filterable table, not a pile of call notes.
- **Repayment reminders.** Call every borrower approaching a due date, and every borrower just past one. Capture intent to pay, a promised date, and the reason for a delay — each answer carrying the sentence from the transcript that supports it, which is what matters when a promise is later disputed.
- **KYC and customer-data refresh.** A regulatory obligation measured in tens of thousands of customers. Confirm address, employment and next-of-kin details at a per-minute cost instead of a per-agent one.
- **Dormant account reactivation.** Call the customers who opened an account and never funded it, or who have gone quiet, and find out why — an answer almost no institution currently has at scale.
- **Product and service outreach.** Savings products, insurance, POS terminals for merchants, agent-banking network check-ins — measuring genuine interest rather than counting dials.
- **Post-resolution follow-up.** Call the customers whose complaints were closed last week and capture whether the problem is actually fixed.

Every one of these is today either an outsourced campaign carrying a per-seat monthly cost, or a thing the institution has quietly stopped doing. On Becca each one is a brief, a spreadsheet and a wallet balance.

### Beyond banking — who is already asking for it

Becca is deliberately domain-agnostic. The agent is built from whatever the brief says, so the product neither knows nor cares which industry it is calling for — which is why the two people who have asked us hardest for it are not banks at all:

- **A consultancy** that qualifies inbound enquiries by phone to decide which are worth a partner's time. Today that is a person on the phone all day: demanding work that does not scale, and that gets less consistent the longer the day runs. Placed instead by an agent that sounds human, the same calls come back as a table they can sort.
- **A theatre producer** staging a concert, who wants to call ticket-holders and ask whether they would upgrade to a higher tier. That is a revenue call worth making only if each one costs cents instead of a salaried hour — which is exactly the arithmetic that keeps it from being made today.

And the rest of the list holds unchanged: a hospital confirming tomorrow's appointments, a school reaching parents about fees, an estate agency sorting 600 campaign leads down to the 40 that are real.

### What this actually eliminates

| The call centre way | Becca |
|---|---|
| Hire, train and supervise a floor of agents; weeks of ramp, and attrition resets it | Describe the call in a sentence; test it in minutes |
| Salaries, supervisors, QA, floor space — a fixed cost that never scales down | Prepaid wallet, per minute, pay only for what you dial |
| A dialer, a CRM, recording, transcription, analytics — five monthly per-seat licences | One system, in which the structured answer *is* the output |
| More volume means more headcount | More volume means a larger wallet balance |
| ~40–60 dials per agent per day, on a shift pattern | Calls placed continuously and concurrently |
| Script drift — every agent asks it slightly differently | Every call runs the same frozen agent version |
| The outcome is a dropdown an agent clicked between calls | One structured row per call, ready to filter and export |
| An unverifiable "she said she was interested" | Every value traced to the transcript that produced it |
| Change the script, retrain everyone | Edit the brief, and the next call already uses it |

And the same system serves the other end of the market unchanged: a business with 600 leads and no call centre gets the identical capability at the cost of 600 calls, with no seats to license and no contract to sign. That is the financial-inclusion half of the argument — the businesses least able to afford idle leads are exactly the ones locked out of the tool that recovers them.

### How it works

1. **Brief.** A client writes what they want the call to do, in plain English.
2. **Agent.** Becca generates an agent from the brief: an ordered **field set** (input fields supplied by the contact list, output fields extracted from the conversation) and a **call guide** — behavioural direction for the call, not verbatim lines. Every edit produces a new immutable agent version.
3. **Test calls.** Real, billed calls to stand-in values the user types. Testing is a loop — output fields are tuned between calls until the answers come back right.
4. **Contacts.** A spreadsheet is uploaded and its columns mapped to the agent's input fields by id. Numbers are normalised to E.164, rows are de-duplicated, and an **import preview** reads three rows back exactly as the agent would speak them.
5. **Launch.** Pre-flight checks run (blocking vs. advisory), the owner records a one-time acknowledgement about their contacts, the output schema freezes, and the agent dials its list exactly once within the contact's local calling window and the run's spend cap. Runs can be paused and resumed, or stopped for good.
6. **Results.** Each call yields one insight result per output field, with provenance into the transcript. Humans can add corrections alongside (never over) the extracted value. "Qualified" is just a saved view the client defines.
7. **Billing.** Each client account has a prepaid USD **wallet**, topped up by staff after a bank transfer, charged a flat per-minute rate for every call (test calls included). The append-only ledger is the source of truth; the console watches Becca's margin against the real telephony cost.

Two roles exist on the client side — **owner** and **member** — and the only permission separating them is launching, because launching is the only action that dials real contacts at scale. Becca staff can *enter* a client account to act on its behalf; that is always visibly signalled and attributed to the staff member.

### Built to be trusted with real money and real phone calls

This is the part a demo doesn't show, and the part that decides whether a system like this can carry a real client:

- **Tenant isolation is structural, not disciplined.** Every tenant-scoped table carries `client_account_id` under Postgres **row-level security**, enabled from migration one. A forgotten `WHERE` clause cannot leak one business's contacts into another's screen.
- **Money is an append-only ledger, never a mutable balance.** Every top-up, call debit and correction is a new line; the balance is a cache of the ledger, and an in-flight call holds its worst-case cost against the wallet so a run can never overspend into the negative.
- **A call is never dialled twice.** Idempotency keys guard both the dispatch queue and Telnyx's webhook redeliveries, so a crash mid-dial or a duplicate callback cannot double-call a person or double-charge a client.
- **Non-production environments cannot dial strangers.** There is a single Telnyx account, so the guardrail lives in the gateway itself: outside production, any number not on an explicit allowlist is refused. Config alone was not trusted with this.
- **People are called at humane hours, with permission.** Calling windows are evaluated in the *contact's* local time, and no client can launch until an owner has acknowledged that the people on their list expect contact from their business.
- **Generation quality is measured, not assumed.** An eval harness (`becca/evals/`) scores generated agents against a rubric, with versioned baselines in `docs/evals/` — so a model or prompt change produces a scorecard, not a hunch.

---

## 🛠️ Tech Stack

* **Frontend:** Server-rendered **Jinja2 + HTMX**, with Alpine.js on the two screens that need real client-side state (the output-field editor and the column mapper). A hand-written design system, no CSS framework.
* **Backend:** **Python 3.14 + FastAPI**, running as two processes — a web app and a dispatcher worker (`python -m becca.worker`). Server-side **Authlib** Google OAuth, signed-cookie sessions, explicit CSRF middleware.
* **Database:** **PostgreSQL**, doing four jobs: relational data, the job queue (`SELECT … FOR UPDATE SKIP LOCKED`), pub/sub for the live call monitor (`LISTEN/NOTIFY` over SSE), and **row-level security** as the tenant boundary. Accessed with **SQLAlchemy 2.0 async + asyncpg**; migrations with **Alembic**.
* **Deployment:** **Render (Frankfurt)** — one web service serving both hostnames via `Host`-header routing, plus a worker and managed Postgres. CI on **GitHub Actions**.
* **AI/APIs:** **Anthropic (Claude)** for agent generation, using structured tool-use output so a brief becomes one validated object rather than parsed prose. **Telnyx** for telephony — AI assistants, outbound calls, and webhooks verified with Ed25519 signatures. Plus `phonenumbers` (E.164 normalisation), `openpyxl`/`csv` (spreadsheets), `tenacity` (retries), and Sentry (observability, with scrubbing configured before the first event).

Every stack decision is numbered `SD-nn` in `docs/techstack.md`, with the alternative we rejected and why; every functional requirement is `FR-*` in `docs/beccavoicefrd.md`.

---

## ⚙️ How to Set Up and Run Locally

**Requirements:** Python 3.14, [uv](https://docs.astral.sh/uv/), Docker.

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Wema-Hackaholics-Hackathon/wema-hackaholics7-0-hackathon-yabatech-project-team-becca.git
   cd wema-hackaholics7-0-hackathon-yabatech-project-team-becca
   ```

2. **Start Postgres** on port 5433 — this also creates the non-superuser `becca_app` role that row-level security depends on:
   ```bash
   docker compose up -d
   ```

3. **Install dependencies:**
   ```bash
   uv sync --all-groups
   ```

4. **Create your `.env`** — the defaults never touch Telnyx and never place a call:
   ```bash
   cp .env.example .env
   ```
   ```
   TELNYX_MODE=fake          # "real" only deliberately, with a human at the keyboard
   DATABASE_URL=postgresql+asyncpg://becca_app:becca@localhost:5433/becca
   ANTHROPIC_API_KEY=        # optional locally; a fake generator runs without it
   DIAL_ALLOWLIST=           # E.164 numbers this environment may dial. Empty = nobody
   ```

5. **Run the migrations:**
   ```bash
   uv run alembic upgrade head
   ```

6. **Start the web app** (both planes) — `app.localtest.me` and `console.localtest.me` both resolve to 127.0.0.1:
   ```bash
   uv run uvicorn becca.web.app:create_app --factory --reload
   ```

7. **Start the worker** in a second terminal — nothing dials without it:
   ```bash
   uv run python -m becca.worker
   ```

> **Safety:** `TELNYX_MODE` defaults to `fake`, so nothing dials. Even in `real` mode, any environment other than production refuses to dial a number that is not on `DIAL_ALLOWLIST` — the guardrail lives in the gateway, not in config someone can forget. See `becca/config.py` for every setting.

### Running the checks

The same gate CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
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
CONTEXT.md        ubiquitous language / glossary
```

`CONTEXT.md` is the glossary for the vocabulary used throughout this project — *brief*, *agent*, *field set*, *call guide*, *run*, *wallet*, *insight result* — and is worth reading first.

---

## 🤝 Contributing

`main` is protected by convention: nobody commits to it directly. All work lands through a pull request that CI has passed and a maintainer has reviewed.

### 1. Start from an issue

Every change should trace back to a GitHub issue — a bug, a feature, or a PRD. If there isn't one, open it first:

```bash
gh issue create --title "Short, imperative summary" --body "What, why, and how you'd know it's done"
```

Issues labelled `ready-for-human` or `ready-for-agent` are fully specified and up for grabs; `needs-triage` and `needs-info` are not ready yet. Assign yourself (`gh issue edit <n> --add-assignee @me`) so two people don't pick up the same thing. See `docs/agents/issue-tracker.md` and `docs/agents/triage-labels.md`.

### 2. Branch off `main`

```bash
git checkout main && git pull
git checkout -b <type>/<issue-number>-<short-slug>
```

Branch types: `feat/`, `fix/`, `chore/`, `docs/`, `refactor/`. Examples: `feat/42-spend-cap-advisory`, `fix/57-webhook-duplicate-debit`. One branch per issue; keep it small enough to review in one sitting.

### 3. Make the change

- Read `CONTEXT.md` before touching any domain concept, and use its vocabulary in code, comments and UI copy — the "_Avoid_" lists are real. If you need a word the glossary doesn't have, add it to `CONTEXT.md` in the same PR.
- Schema changes go through Alembic: `uv run alembic revision -m "short description"`, numbered after the latest in `alembic/versions/`. Every tenant-scoped table carries `client_account_id` and an RLS policy — no exceptions.
- Anything that touches dialling, billing or webhooks needs a test. The fakes (`becca/telnyx/fake_gateway.py`, `becca/generation/fake.py`) exist so nothing in `tests/` ever reaches Telnyx or Anthropic.
- A decision with a real alternative you rejected deserves an ADR in `docs/adr/` (copy the shape of `0001-prepaid-wallet-ledger.md`). A contradiction you notice between the docs goes in `docs/open-conflicts.md` — don't silently pick a side.
- Never dial a real number from a dev or staging environment that isn't on your own `DIAL_ALLOWLIST`.

### 4. Run the checks locally

CI runs exactly this; save yourself the round-trip:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

`uv run ruff format .` fixes formatting in place.

### 5. Commit

Small, focused commits with an imperative subject line (≤ 72 chars) and a body that explains *why*, not what — the diff already shows what. Reference the issue in the body (`Closes #42`) so GitHub links and auto-closes it.

```
Refuse launch when the spend cap is below one hold

A cap smaller than rate × max_call_minutes could never admit a single
call, so the run would pause on its first dial. Make it a blocking
pre-flight condition instead of letting the owner discover it live.

Closes #42
```

### 6. Open a pull request

```bash
git push -u origin <your-branch>
gh pr create --fill        # or --title/--body; the template will prompt for the rest
```

The PR template asks for: the issue it closes, what changed and why, how you tested it (including whether any real call was placed, and to which allowlisted number), and any follow-ups you deliberately left out. A PR is ready for review when CI is green and the template is filled in.

Review is by at least one maintainer. Respond to comments with new commits rather than force-pushes, so the reviewer can see what moved. Once approved, a maintainer **squash-merges** into `main` — the PR title and body become the commit message, so write them as you'd want them read in `git log` a year from now.

### What reviewers look for

- Does it do what the issue asked — no more, no less?
- Could it leak one client account's data into another? (RLS present, `client_account_id` set, no cross-tenant query.)
- Could it double-dial, double-charge, or dial outside the allowlist?
- Does the UI copy use the glossary's words?
- Is there a test that would fail if this broke?

---

## 📚 Working on the project

- **Read before changing behaviour:** `CONTEXT.md`, `docs/beccavoicefrd.md`, `docs/techstack.md`, and `docs/open-conflicts.md` (known contradictions between the documents). `docs/agents/domain.md` explains how to use them.
- **Secrets** never go in `.env.example`. `.env` and `.env.staging` are gitignored and must stay so.
