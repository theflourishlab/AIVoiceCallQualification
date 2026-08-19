## Closes

Closes #

## What changed, and why

<!-- A short paragraph a reviewer can read before the diff. Why this approach over the obvious alternative? -->

## How I tested it

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest` pass locally
- [ ] New or changed behaviour has a test that would fail without this change
- [ ] No real call was placed — or: a test call was placed to an allowlisted number (which: `+234…`)

## Checklist

- [ ] Uses the vocabulary in `CONTEXT.md` (and updates it if a new term was needed)
- [ ] Any new tenant-scoped table has `client_account_id` and an RLS policy
- [ ] No secrets, `.env*`, logs or spike output in the diff
- [ ] Migration added if the schema changed (`alembic/versions/`)
- [ ] ADR added in `docs/adr/` if a real alternative was rejected

## Deliberately left out / follow-ups

<!-- Anything you chose not to do here and why, so it isn't read as forgotten. -->
