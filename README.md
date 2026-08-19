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

## Working on the project

- **Issues and PRDs** are GitHub issues managed with `gh` — see `docs/agents/issue-tracker.md` and `docs/agents/triage-labels.md`.
- **Read before changing behaviour:** `CONTEXT.md`, `docs/beccavoicefrd.md`, `docs/techstack.md`, and `docs/open-conflicts.md` (known contradictions between the documents). `docs/agents/domain.md` explains how to use them.
- **Secrets** never go in `.env.example`. `.env` and `.env.staging` are gitignored and must stay so.
