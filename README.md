# Becca — AI Voice Call Qualification

Becca lets a business owner describe a phone call in plain English, then builds a voice agent that places that call to an imported contact list and brings back structured answers — one row of results per person called, with every extracted value linked back to the moment in the transcript that produced it.


## Live deployment

| Plane | URL | Who uses it |
|---|---|---|
| **Client app** | [https://app.becca.live](https://app.becca.live) | Client businesses — build agents, import contacts, run test calls, launch, read results, see what they owe |
| **Becca console** | [https://console.becca.live](https://console.becca.live) | Becca staff only — every client account, wallets and top-ups, per-minute rates, the margin monitor, number inventory, channel allocation |

Both hostnames point at the **same deployment**; FastAPI routes on the `Host` header to one of two separate applications that share a database. Sign-in is Google OAuth; there is no sign-up — a user exists only if staff added their email.

## How it works

1. **Brief.** A client writes what they want the call to do, in plain English.
2. **Agent.** Becca generates an agent from the brief: an ordered **field set** (input fields supplied by the contact list, output fields extracted from the conversation) and a **call guide** — behavioural direction for the call, not verbatim lines. Every edit produces a new immutable agent version.
3. **Test calls.** Real, billed calls to stand-in values the user types. Testing is a loop — output fields are tuned between calls until the answers come back right.
4. **Contacts.** A spreadsheet is uploaded and its columns mapped to the agent's input fields by id. Numbers are normalised to E.164, rows are de-duplicated, and an **import preview** reads three rows back exactly as the agent would speak them.
5. **Launch.** Pre-flight checks run (blocking vs. advisory), the owner records a one-time acknowledgement about their contacts, the output schema freezes, and the agent dials its list exactly once within the contact's local calling window and the run's spend cap. Runs can be paused and resumed, or stopped for good.
6. **Results.** Each call yields one insight result per output field, with provenance into the transcript. Humans can add corrections alongside (never over) the extracted value. "Qualified" is just a saved view the client defines.
7. **Billing.** Each client account has a prepaid USD **wallet**, topped up by staff after a bank transfer, charged a flat per-minute rate for every call (test calls included). The append-only ledger is the source of truth; the console watches Becca's margin against the real telephony cost.

Two roles exist on the client side — **owner** and **member** — and the only permission separating them is launching, because launching is the only action that dials real contacts at scale. Becca staff can *enter* a client account to act on its behalf; that is always visibly signalled and attributed to the staff member.

`CONTEXT.md` is the glossary for all of this vocabulary and is worth reading first.

## Stack

- **Python 3.14 · FastAPI · Jinja2 · HTMX** (Alpine on two screens) — server-rendered; two Python processes and a Postgres.
- **PostgreSQL** — data, job queue (`FOR UPDATE SKIP LOCKED`), pub/sub for the live monitor, and **row-level security** as the tenant boundary from migration one.
- **SQLAlchemy 2.0 async + asyncpg**, Alembic migrations.
- **Telnyx** for telephony (AI assistants, calls, webhooks verified with Ed25519); **Anthropic** for agent generation via structured tool-use output.
- **Authlib** (Google OAuth, server-side), signed-cookie sessions, explicit CSRF middleware, Sentry with scrubbing.
- Hosted on **Render (Frankfurt)**; CI on GitHub Actions.

Every stack decision is numbered `SD-nn` in `docs/techstack.md`; every functional requirement is `FR-*` in `docs/beccavoicefrd.md`.

## Repository layout

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

## Running locally

Requirements: Python 3.14, [uv](https://docs.astral.sh/uv/), Docker.

```bash
# 1. Postgres on :5433 — creates the non-superuser becca_app role (RLS depends on it)
docker compose up -d

# 2. Dependencies
uv sync --all-groups

# 3. Config — the defaults never touch Telnyx
cp .env.example .env

# 4. Migrate
uv run alembic upgrade head

# 5. Web (both planes) — app.localtest.me / console.localtest.me resolve to 127.0.0.1
uv run uvicorn becca.web.app:create_app --factory --reload

# 6. Worker, in a second terminal
uv run python -m becca.worker
```

`TELNYX_MODE` defaults to `fake`, so nothing dials. Outside production, even `real` mode refuses any number not on `DIAL_ALLOWLIST` (comma-separated E.164) — there is a single Telnyx account, so this guardrail lives in the gateway, not in config that can be forgotten. See `becca/config.py` for every setting.

## Checks

The same gate CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

## Contributing

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

## Working on the project

- **Read before changing behaviour:** `CONTEXT.md`, `docs/beccavoicefrd.md`, `docs/techstack.md`, and `docs/open-conflicts.md` (known contradictions between the documents). `docs/agents/domain.md` explains how to use them.
- **Secrets** never go in `.env.example`. `.env` and `.env.staging` are gitignored and must stay so.
