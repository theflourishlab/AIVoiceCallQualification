# Stack Decisions
## Becca — AI Voice Call Qualification

**Version** 1.0 · **Date** 4 August 2026 · Companion to `FRD-v1.md`

> **⚠ Check the Amendment log at the end of this document before acting on any billing-related decision.** Since 14 Aug 2026, client billing is a prepaid wallet at a flat per-minute rate (FRD Amendment A1); the invoice-generation rows below (SD-18, the invoicing/cost-sync jobs, the storage table) describe a superseded model.

Every decision is numbered `SD-nn` so it can be cited in review. Status is **Settled**, **Recommended** (my call, awaiting yours), or **Open** (genuinely yours).

---

## The number that anchors everything

**The hard ceiling is 10 concurrent calls.** Not ten thousand requests per second. Ten phone calls, enforced by Telnyx account-wide, treated as fixed.

Almost every scaling decision below is therefore moot before it starts. The bias throughout is fewer moving parts, and I would push back on anything that adds infrastructure to solve a problem this system cannot have.

**Decided so far:** SD-07 HTMX · SD-08 Render · SD-09 Postgres queue · SD-10 row-level security · SD-12 Authlib.

**Still open:** SD-11 data access layer, and SD-13 the staging guardrail. Nothing else blocks Phase 0.

---

## A. Settled

| # | Decision | Choice | Why |
|---|---|---|---|
| SD-01 | Language | Python | Your primary |
| SD-02 | Web framework | FastAPI | Your primary |
| SD-03 | Database | Postgres | Relational data, JSONB for field sets, and it doubles as queue and pub/sub |
| SD-04 | LLM | Anthropic, structured output via tool use | FR-AGENT-4 requires one validated object, not parsed prose |
| SD-05 | Region | EU — Frankfurt, per SD-08 | Amsterdam is ~16 ms better for Lagos but Render is Frankfurt-only, and managed Postgres won the trade |
| SD-06 | Design system | Becca v3 | `becca.css` already extracted, 26KB, no framework |
| SD-07 | Frontend | FastAPI + Jinja + HTMX, Alpine on two screens | Decided |
| SD-08 | Hosting | Render, Frankfurt | Decided |
| SD-10 | Tenant isolation | Postgres row-level security | Decided |
| SD-12 | Auth | Authlib, server-side Google OAuth | Follows from SD-07 |

---

## B. Consequential, needs your call

### SD-07 · Frontend approach — **DECIDED: FastAPI + Jinja + HTMX, with Alpine on two screens**

Twelve of twenty-two screens are forms and tables. One streams. Only two need real client-side state: the output field editor and the column mapper, both solvable with a few dozen lines of Alpine.

Choosing this collapses deployment to two Python processes and a Postgres, removes the API contract, removes CORS and token handling, and keeps the auth rule in one place. Already proven: the extracted stylesheet renders the agents screen pixel-identical from a Jinja template.

Choose Next.js instead if a JavaScript-primary developer is building it, if mobile is near-term rather than someday, or if this frontend becomes the shell for other Becca products.

### SD-08 · Hosting — **DECIDED: Render, Frankfurt, ~$21.50/mo**

**Custom domains: yes, on every option, and two hostnames can point at one service.** So `app.becca.live` and `console.becca.live` are two CNAMEs on the same web service, routed by `Host` header inside FastAPI. One deploy, two domains. Render includes 2 custom domains on Hobby with automatic Let's Encrypt, which is exactly what we need.

Verified August 2026:

| | Railway | Render | Fly.io | Hetzner + Coolify |
|---|---|---|---|---|
| Monthly, EU, never sleeping | $15–22 | **$21.50** | $47 (managed PG is $38 of it) | **€6** |
| Sleeps? | No, opt-in only | Free tier yes, paid never | **Yes by default**, must disable | No |
| Managed Postgres | **"Unmanaged" per their own docs** | $6/mo, real managed | $38/mo | You own it |
| EU region | Amsterdam | Frankfurt | ams, fra, others | Falkenstein, Nuremberg, Helsinki |
| Lagos RTT | **~110 ms** | ~127 ms | ~110 ms from ams | ~125 ms |

**Why Render.** `FR-RESULT-1` makes our database the system of record precisely because Telnyx retention is undocumented. Railway's own docs describe their Postgres as *unmanaged*, meaning you own version upgrades and maintenance. For $6/mo more, Render gives real managed Postgres with point-in-time recovery. That is the whole argument, and it outweighs the rest.

**On the 16 ms.** Amsterdam beats Frankfurt for Lagos, 110 ms against 127 ms. On a cold page load that is roughly 50 ms once you count DNS, TCP and TLS round trips; on a warm connection it is 16 ms per request and imperceptible for server-rendered HTML. Not enough to accept unmanaged Postgres for.

**On cold starts, since you asked about wake-up.** Render's **free** tier sleeps after 15 minutes and takes about a minute to wake, which would silently drop Telnyx webhooks. Paid instances never sleep. So use Starter, not Free, and this stops being a consideration. Two adjacent traps worth knowing: Railway's opt-in sleep returns **502 on the first request**, and Fly's autostop is **on by default** for new apps. Both would break a webhook endpoint quietly.

**Hetzner is 3x cheaper and I am not recommending it for v1.** €6/mo against $21.50 is real money proportionally and irrelevant absolutely. What you take on is Postgres backups *and restore testing*, OS patching, TLS renewal monitoring, disk-full alerting, and being on call for a single point of failure, while trying to ship fast for a client. Revisit at three or four clients when the saving compounds and the ops are amortised.

**Render's one real weakness** is that its zero-downtime deploys apply to background workers too, so old and new run concurrently for a moment. That used to matter. See SD-09.

### SD-09 · Dispatcher runtime — **Postgres as the queue, and the governor moves into Postgres too**

`SELECT ... FOR UPDATE SKIP LOCKED`, worker process, no Redis and no broker. At ten in-flight calls the whole account fits in one process, and restart safety comes from the row lock.

**Amended after the hosting research.** I originally specified the concurrency governor as an in-process semaphore per client, which forced exactly one worker instance. That constraint fights every managed platform: **Render and Railway both overlap old and new during a deploy**, so two workers briefly exist and you get two semaphores and double the intended concurrency. Only Fly and a self-managed VM give true stop-then-start.

Rather than choose a platform to satisfy an implementation detail, move the governor into the database:

```sql
-- before dispatching, inside the same transaction as the row lock
SELECT count(*) FROM call
 WHERE client_account_id = $1 AND status = 'dialling';
-- proceed only if below that client's allocation
```

One query, correct regardless of how many workers exist, and deploy overlap becomes harmless. It also means the allocation is enforced by the same mechanism that records the calls, so the two cannot disagree.

**This removes the single-worker constraint from SD-08 entirely**, which is why Render's deploy overlap is no longer a mark against it.

### SD-10 · Tenant isolation — **DECIDED: Postgres row-level security from migration one**

`client_account_id` on every tenant-scoped table, RLS policies enabled even with a single tenant. A forgotten `WHERE` clause then cannot leak. Retrofitting at client three means auditing every query already written.

We designed hard against cross-client leaks in the FRD; this is the mechanism that makes it structural rather than disciplined.

### SD-11 · Async or sync, and the data access layer — **Recommended: SQLAlchemy 2.0 async + asyncpg**

These are one decision, not two. The dispatcher is IO-bound HTTP wanting async, and RLS needs `SET LOCAL app.client_id` inside every transaction, which couples session handling to tenancy enforcement.

Alternatives are raw asyncpg (faster, more hand-written SQL) or SQLModel (lighter, thinner async story). Whatever you pick, decide it first: it shapes every module.

### SD-12 · Auth — **DECIDED by SD-07: Authlib, server-side**

Our rule is one lookup: authenticate, find the email, refuse if absent. No sign-up, no password reset, no invitations. Roughly a day including session cookies.

Firebase would only have made sense with Next.js. On a server-rendered app you would fight its client-SDK model and still write the membership lookup yourself. SD-07 settled this.

### SD-13 · Staging, given one Telnyx account — **Open, and I would not skip it**

You have one Telnyx account, so staging points at the same key, the same numbers, the same balance. A dispatcher bug in staging dials real Nigerian mobiles.

The guardrail must live in the dispatcher, not in config someone can forget: in any non-production environment, refuse to dial a number not on an allowlist. Decide now, because trust is hard to rebuild after the first accidental call.

---

## C. Mechanical, decide once and write down

| # | Decision | Recommendation | Note |
|---|---|---|---|
| SD-14 | Migrations | Alembic | |
| SD-15 | Telnyx HTTP client | `httpx`, wrapped in one function per endpoint | See below |
| SD-16 | Retry and backoff | `tenacity`, with a strict retryable/not-retryable split | See below |
| SD-17 | Object storage | **None. Store both blobs in Postgres** | See below |
| SD-18 | Invoice PDFs | WeasyPrint | HTML to PDF, so invoices reuse the design system |
| SD-19 | Spreadsheet parsing | stdlib `csv` + `openpyxl` | |
| SD-20 | Phone normalisation | `phonenumbers` | Runs before the dedupe key is computed (FR-CONTACT-8) |
| SD-21 | Timezones | `zoneinfo`, `timestamptz` everywhere | Calling windows are in the contact's local time; source it from the client account |
| SD-22 | Periodic jobs | **No scheduler library.** Housekeeping runs in the worker's existing loop | See below |
| SD-23 | Live monitor transport | SSE + Postgres `LISTEN/NOTIFY` | One direction, no message bus. Polling would also be fine at this scale |
| SD-24 | Sessions | Signed cookie, `itsdangerous`, with `HttpOnly` + `Secure` + `SameSite=Lax` | See below |
| SD-25 | CSRF | Explicit middleware | FastAPI does not include it, and server-rendered forms need it. Easy to ship without noticing |
| SD-26 | Webhook handling | Ed25519 signature verification + idempotency key | Telnyx redelivers; a duplicate must not double-count a call or double-charge a client |
| SD-27 | Dispatch idempotency | Idempotency key per queue item | A crash between "row locked" and "call placed" must not dial twice. Doubly important now that deploy overlap is tolerated (SD-09) |
| SD-28 | Observability | Sentry free tier, **scrubber configured before the first event is ever sent** | See below |
| SD-29 | Secrets | Whatever SD-08 provides | One Telnyx key; plan rotation |
| SD-30 | CI | GitHub Actions | |
| SD-36 | Audit log | Append-only table | FR-CONSOLE-8 |
| SD-37 | Charts | Plain divs, no library | Already decided by the design: the prototype's bars are `<div>`s |
| SD-38 | Environments | dev, staging, production | Staging governed by SD-13 |

### On SD-13: what a staging guardrail is, and why config is the wrong place for it

**Staging** is a second copy of the app, running the same code, used to try things before they reach clients. Normally it is harmless because it has its own database and its own accounts.

**Ours is not harmless, because there is only one Telnyx account.** Staging would use the same API key, the same phone numbers and the same balance as production. So a half-finished dispatcher running in staging can dial real Nigerian mobiles belonging to a real client's customers. It does not feel like a live system while you are working on it, which is exactly what makes it dangerous.

**The guardrail** is a rule that stops any non-production environment from dialling a number that is not on a short allowlist of your own phones.

**Why not put it in configuration.** The obvious version is an environment variable, `ALLOW_REAL_CALLS=false`. That fails in ordinary ways: someone clones the production environment to create staging and copies the variable set with it, or the variable is simply never set on a new environment and the code has to guess a default. The protection is then only as good as a checklist.

**In the dispatcher instead** means the function that places calls asks, every single time, before every call:

```python
if settings.env != "production" and to_number not in SAFE_NUMBERS:
    raise RefusedOutsideProduction(to_number)
```

It is not a setting anyone can forget to switch on, because it is always on. Bypassing it means deleting code, deliberately, in a diff someone reviews.

**And it must fail closed.** If `env` is missing, unrecognised or empty, refuse to dial. A misconfigured environment then produces zero calls, which you notice in seconds, rather than wrong calls, which you notice when a client rings you.

### On SD-24: what a signed cookie is, and no, it is not dangerous

A **cookie** is a small piece of data the browser stores and sends back with every request. We use one to remember that this browser belongs to a signed-in user.

**Signed** means we attach a cryptographic signature to the contents. If anyone edits the cookie, say changing the user id to somebody else's, the signature stops matching and we reject it. `itsdangerous` is the library that does the signing; the alarming name is about the data, not the technique.

**The one thing to understand: signed is not encrypted.** The contents are readable by whoever holds the cookie. So it carries a user id and an expiry, and nothing else. No email, no role, no client account name.

Three flags make it safe, and all three are non-negotiable:

| Flag | Stops |
|---|---|
| `HttpOnly` | JavaScript reading the cookie, so a script injection cannot steal the session |
| `Secure` | The cookie ever travelling over plain HTTP |
| `SameSite=Lax` | Another site making authenticated requests on the user's behalf |

**On revocation, which is the usual objection.** With a signed cookie, removing someone from the database does not by itself invalidate their cookie. It does not matter here, because every request already loads their memberships from the database to know what they can see. A removed user's next request finds no membership and is refused. Access ends immediately.

The alternative, storing sessions server-side, would be fine too and buys nothing extra given the above. Signed cookie means one fewer table.

### On SD-28: why error tracking, and why the scrubber is the actual requirement

**Why error tracking at all.** The worker runs unattended for days. If it throws an exception mid-run, dialling stops and nobody is watching a terminal. The client sees an agent that quietly stopped; you see nothing until someone asks. Render streams logs, but a log stream does not wake you up, and grepping it assumes you already know something went wrong. Something has to actively tell you.

**Why the scrubber is not a nice-to-have.** Sentry's value is that it captures context automatically: the request body, local variables, headers. In this system those contain:

| Route | What is in the request body |
|---|---|
| `call.conversation_insights.generated` webhook | Extracted answers about a named person |
| Transcript fetch and mirror | **The entire recorded conversation, verbatim** |
| Dial | The contact's phone number, name and appointment details |
| Contact import | A whole spreadsheet of personal data |

So an unhandled exception in the insights webhook would, by default, ship a Nigerian consumer's full conversation to a US vendor. Not through malice, just through a stack frame holding a variable.

Under the NDPA that is personal data crossing a border to a processor nobody disclosed. And it is not a hypothetical: it is the default behaviour of the tool unless you turn it off.

**What "configure a scrubber" literally means.** Sentry lets you register a function that runs on every error report *before* it leaves your server. You edit the report in that function, or return `None` to drop it entirely. That function is the scrubber. It is about twenty lines:

```python
import re, sentry_sdk

E164 = re.compile(r"\+?\d{10,15}")

def scrub(event, hint):
    # 1. never send request bodies, cookies or query strings
    if "request" in event:
        for key in ("data", "cookies", "query_string", "headers"):
            event["request"].pop(key, None)

    # 2. drop local variables from stack frames — this is where a
    #    transcript sits when the insights handler throws
    for exc in event.get("exception", {}).get("values", []):
        for frame in exc.get("stacktrace", {}).get("frames", []):
            frame.pop("vars", None)

    # 3. mask anything phone-shaped in messages and breadcrumbs
    def mask(o):
        if isinstance(o, str):  return E164.sub("[phone]", o)
        if isinstance(o, dict): return {k: mask(v) for k, v in o.items()}
        if isinstance(o, list): return [mask(v) for v in o]
        return o

    return mask(event)

sentry_sdk.init(
    dsn=settings.sentry_dsn,
    before_send=scrub,
    send_default_pii=False,   # do not attach IP addresses or user data
)
```

Line 2 is the one that matters most. When an exception is raised, Sentry captures the **local variables of every function on the stack**. In the insights webhook handler, one of those locals is the transcript.

**"Before the first event" means this lives inside `sentry_sdk.init()`**, which runs at process startup, so it is active before any code can throw. The risk is not ordering within the file. It is shipping to production having never written the function, and discovering a customer's conversation in an error report afterwards.

**What must be scrubbed, specifically:**

- Request bodies on **all** webhook routes, wholesale rather than field by field
- `text` on any conversation message
- Anything matching an E.164 pattern, anywhere including breadcrumbs and log lines
- `AIAssistantDynamicVariables`, which carries names and appointment details
- Uploaded file contents

**Why "before the first event".** Sentry keeps what it receives. You cannot recall it. Configuring the scrubber after you notice a transcript in an error report does nothing about the transcripts already sent, and the honest remediation at that point is deleting the Sentry project.

**The vendor is not the decision; the scrubbing is.** Any error tracker would do, and the free tier covers this volume comfortably. What must not happen is shipping without the scrubber because it felt like a day-two task.

### On SD-22: what "periodic jobs" even means, and why we now have none

**What the row is about.** Most code runs because someone did something: a user clicks Launch, Telnyx sends a webhook. A **periodic job** is the other kind, code that runs on a timer with nobody present. A cleaner that comes at 3am. The question SD-22 asks is: do we need a timer system, and if so which one.

**My original answer was APScheduler**, a Python library that runs functions on a schedule. **The answer is now: nothing.** No library, no cron, no timer at all.

I listed APScheduler without checking whether anything actually needed scheduling. Going through the four candidates, none does.

| Job | Why it does not need a scheduler |
|---|---|
| **Orphan sweeper** — delete scratch assistants and their TeXML apps | The worker already runs forever polling the queue. This is "every hundredth iteration, sweep." A counter, not a cron |
| **Invoice generation** | Monthly, and there is already a *Generate invoices* button on console screen 19. With three clients, that is a click, not an automation |
| **Cost sync from detail records** | Pull when the console billing screen loads, scoped to the period being viewed. Nobody needs yesterday's cost recomputed at 3am while nobody is looking |
| **Telnyx balance check** | Same. Read it when the console landing screen renders, which is exactly where FR-NOTIFY-2A says it must be visible |

**And the two things that sound like scheduling are not.** A calling window of 09:00 to 18:00 is the worker checking the clock before it dials, not a trigger that fires at nine. An agent scheduled for Monday morning is a `not_before` timestamp on its queue rows, which the worker's normal query already respects. In both cases the worker is running continuously anyway; it simply declines to dial.

So: no APScheduler, no platform cron, no `pg_cron`. One fewer dependency, and no second process that could reintroduce the concurrency question SD-09 just closed.

**The one honest tradeoff:** if the worker is down, housekeeping stops. That is acceptable because dialling stops too, which is far more noticeable, so you would already be looking.

### On SD-17: Render has no object storage, and we do not need any

**Answer to the direct question: no.** Render offers *Persistent Disks*, not S3-compatible object storage. Cloud object storage is an open feature request, not a product. There is a MinIO template if you want to self-host object storage on Render, which is another service to run and monitor.

**And Persistent Disks would be the wrong tool anyway.** From their docs, a service with a disk attached **cannot scale to multiple instances** and **loses zero-downtime deploys**, because Render stops the old instance before starting the new one to avoid two versions writing to the same filesystem. Accepting both of those to store a few megabytes would be a poor trade.

> Ironically that stop-before-start behaviour is exactly what I originally wanted for the worker. We no longer need it, because SD-09 moved the concurrency governor into Postgres.

**So how much do we actually store?** Only two things, and recordings are not among them (FR-REC-6 leaves those with Telnyx):

| Blob | Size | A year of it |
|---|---|---|
| Uploaded CSV, 683 rows | ~50 KB | 200 lists ≈ 10 MB |
| Invoice PDF | ~30 KB | 3 clients × 12 months ≈ 1 MB |

Single-digit megabytes per year. That is a `bytea` column, not a storage tier. Postgres handles it without noticing, it is covered by the same backups and point-in-time recovery as everything else, and it means no second vendor, no bucket credentials, no lifecycle policy and no signed-URL code.

Keeping the invoice PDF rather than regenerating it preserves FR-BILL-7's guarantee that a reissued invoice is byte-identical to one a client already downloaded.

**The trigger to revisit is specific:** if we ever decide to mirror recordings ourselves, that is roughly 1 MB per minute, so about 2 GB a month for one client. Binary data at that scale does not belong in Postgres, and that is the point at which R2 or S3 earns its place. Until then, adding it would be infrastructure for a problem we have deliberately arranged not to have.

### On SD-15, and what a "thin wrapper" is

`httpx` is a general HTTP library, the async-capable successor to `requests`. A **thin wrapper** is one small function per Telnyx endpoint that does the HTTP call and nothing clever:

```python
async def place_call(connection_id: str, to: str, from_: str,
                     assistant_id: str, variables: dict, record: bool) -> dict:
    r = await client.post(f"/texml/ai_calls/{connection_id}", json={
        "From": from_, "To": to, "AIAssistantId": assistant_id,
        "AIAssistantDynamicVariables": {k: str(v) for k, v in variables.items()},
        "MachineDetection": "DetectMessageEnd",     # FR-DISPATCH-4
        "DetectionMode": "Premium",                 # never optional
        "AsyncAmd": True,
        "Record": record,                           # FR-REC-2
    })
    r.raise_for_status()
    return r.json()
```

**The reason is not to avoid the SDK. It is to have one place where our own rules live.**

FR-DISPATCH-4 says every outbound call must carry the AMD block, because Telnyx defaults it to off and the agent would otherwise hold conversations with answerphones. FR-REC-2 says recording comes from the per-call flag. FR-DISPATCH-5 says metadata must be strings. If call sites talk to Telnyx directly, every one of those is a rule someone has to remember. Behind `place_call()` they are impossible to forget, and the requirement number sits in a comment next to the line that implements it.

Two secondary reasons: we use roughly fifteen of Telnyx's 806 endpoints, so an SDK is mostly surface we do not need; and SD-11 makes everything async, so a synchronous client would need wrapping regardless. If the official SDK turns out to have good async support, build the wrappers on top of it. **The wrapper layer is the decision; the client underneath it is not.**

### On SD-16, what `tenacity` is, and the part that matters

`tenacity` is a small Python library for retrying a function that failed. You decorate it and it handles waiting, exponential backoff, jitter, a maximum attempt count and a deadline, rather than you writing that loop by hand and getting the jitter subtly wrong.

**The part that actually matters is not the retrying, it is knowing what must never be retried.**

| Failure | Retry? | Why |
|---|---|---|
| Connection refused, DNS failure, timeout **before** the request was sent | Yes | Telnyx never saw it. Nothing happened. |
| `429` rate limit, `503 CPS limit reached` | Yes, with backoff | Explicitly transient. Unlikely at ten concurrent calls, but free to handle. |
| `5xx` from Telnyx | Yes, cautiously | See the row below. |
| `4xx` other than 429 | **Never** | A malformed request will be malformed the second time too. |
| **Timeout after the request was sent** | **Never blindly** | This is the dangerous one. |

That last row is why SD-27 exists. If we send a dial request and the connection drops before the response arrives, **the call may have been placed.** Retrying rings a real person a second time. So the wrapper must distinguish "did not reach Telnyx" from "reached Telnyx, answer lost", and the second case resolves by reconciling against Telnyx rather than by retrying: query for a call matching our idempotency key before dialling again.

A retry library that does not know this distinction will cheerfully double-dial your client's customers.


## What is no longer on this list

| Removed | Why |
|---|---|
| Transactional email provider | Notifications are in-app only (FR-NOTIFY-1). Deletes provider selection, domain verification, SPF, DKIM, DMARC, bounce handling and DNS lead time |
| Background job framework for exports | Bulk recording export cut (FR-REC-5) |
| Recording storage and lifecycle | Becca does not own retention in v1 (FR-REC-6) |
| Redis or a message broker | Ceiling of ten calls (SD-09) |
| Chart library | Design already answers it (SD-37) |
| Job scheduler | Nothing needs one; the worker loop already runs continuously (SD-22) |
| Object storage, buckets, signed URLs | Only two small blobs exist and they live in Postgres (SD-17) |

---

## Decide in this order

1. ~~SD-07 frontend~~ · ~~SD-08 hosting~~ · ~~SD-09 dispatcher~~ · ~~SD-10 RLS~~ · ~~SD-12 auth~~ — **done**
2. **SD-11 data access layer** — the last one that shapes every module
3. **SD-13 staging guardrail** — write it into the dispatcher on day one, not later
4. The rest as you reach them

Then Phase 0: create an assistant, place one call with AMD, receive the webhooks, read the insight back.
---

## Amendment log

| Date | Scope | Summary |
|------|-------|---------|
| 2026-08-14 | SD-18, "Invoice generation" and "Cost sync" rows, storage table | **Wallet billing shipped** (FRD Amendment A1). Invoice generation is REMOVED — no button, no monthly click; pre-wallet invoices are frozen receipts (their stored PDFs still satisfy byte-identical reissue, so the SD-18/WeasyPrint note is history — fpdf2 was used, and nothing renders invoices anymore). Cost sync survives unchanged as Becca''s internal margin monitor. New table `wallet_ledger` (append-only via raising trigger — Postgres refuses ON CONFLICT alongside rules, recorded in migration 0011). Client billing is a prepaid wallet at a flat per-minute rate; see `docs/adr/0001-prepaid-wallet-ledger.md`.
