# Becca — AI Voice Call Qualification

A web application where a business owner describes a phone call in plain English and the system builds a voice agent that places that call to an imported contact list and returns structured answers. Becca operates it as an agency: one telephony account serves several client businesses, who never see each other, never see cost, and never touch infrastructure.

## Language

### The two planes

**Plane**:
One of the two surfaces the system presents. They are separate applications sharing a database, not one application with role-based hiding.
_Avoid_: side, mode, view

**Becca console**:
The agency-only plane, spanning every client. Holds cost, wallet balances, per-minute rates, the margin monitor, legacy invoices, number inventory and channel allocation.
_Avoid_: admin, backoffice, superadmin, internal dashboard

**Client account**:
One client business, and the tenant boundary. Holds their people, agents, contacts, results and what they owe, and must never reveal that another client exists.
_Avoid_: workspace, tenant, organisation, company, team

**Entering**:
A Becca staff member operating inside a client account on that client's behalf. Always visibly signalled and always attributed to the staff member, never to the client.
_Avoid_: impersonation, sudo, switching, masquerading

### People

**Becca staff**:
A person who works for the agency. One role with full access across every client account.
_Avoid_: admin, operator, agent (that word is taken)

**Owner**:
A client user who may do everything a member can, plus launch, pause and stop agents, and see the bill.

**Member**:
A client user who may build agents, run test calls, import contacts, and read and export results — but never launch. Launching is the only permission separating the two roles, because it is the only action that dials real contacts at scale. (Test calls spend wallet money too, and members deliberately keep them — testing is the build loop.)

### Building an agent

**Agent**:
The thing a user builds and the thing that dials — one object, not two. Carries a status, and dials exactly one contact list exactly once.
_Avoid_: bot, campaign, assistant (reserved for the telephony object), workflow

**Brief**:
The free-text description of the call a user wants, written in plain English. The single input from which an agent is generated.
_Avoid_: prompt, spec, description, requirements

**Field set**:
The single ordered list of an agent's fields. Together with the script it is the whole of what an agent version stores about its content — everything else is derived from it.
_Avoid_: schema, definition, spec

**Field**:
One named thing the agent either needs or captures. `kind: input` is supplied by the contact list; `kind: output` is extracted from the conversation. Both kinds live in one list with unique keys, so a name can never be both.
_Avoid_: variable, column, parameter, attribute

**Call guide**:
The agent's behavioural direction for the call — what to say and how to behave, never verbatim lines — stored as an ordered sequence of script blocks rather than as text. Renamed from "call script" (14 Aug 2026): "script" wrongly implied word-for-word reading. Code identifiers keep the historical name (`script_blocks`, `ScriptBlock`, `scriptfmt`); "instructions" remains reserved for the derived rendering sent to the telephony assistant.
_Avoid_: call script, script, prompt, system prompt, template, instructions

**Script block**:
One piece of a call guide (the storage unit — internal vocabulary, never shown to clients): either literal text, or a `field_ref` pointing at a field **by id**. A field's name is never written into the guide, which is why renaming a field touches nothing else.
_Avoid_: token, placeholder, segment, chunk

**Chip**:
How a `field_ref` appears in the script editor. Behaves as one indivisible character — the cursor sits before or after it, never inside, and backspace removes it whole.
_Avoid_: tag, pill, token, variable

**Variable contract**:
A *view* over the field set — `fields where kind = input`. Computed whenever needed and never stored, so it cannot disagree with the field set.
_Avoid_: required columns, input schema, contract document

**Insight schema**:
A *view* over the field set — `fields where kind = output`. Frozen at launch for the life of the run. Also never stored separately.
_Avoid_: output schema, extraction schema, questions, form

**Insight**:
An output field as the telephony provider represents it — one entry in the run assistant's insight group. Ours is a field; theirs is an insight.

**Agent version**:
An immutable snapshot of an agent's field set, script and settings. Editing an agent produces a new version rather than mutating the current one.
_Avoid_: revision, draft, iteration

### Calling

**Run**:
The single dialling lifecycle of an agent. A lifecycle, not an entity — there is no run record, and an agent has exactly one run.
_Avoid_: campaign, batch, job, execution

**Test call**:
A real, billed call placed while building an agent, using stand-in values the user types, and excluded from run results. Billed like any call: per minute, from the wallet, with a hold while dialling. Testing is a loop rather than a checkpoint: the output fields are edited between calls until the answers come back right.
_Avoid_: dry run, simulation, preview, sandbox call

**Contact list**:
An imported set of people belonging to the one agent it was mapped for, because the agent determines which columns are required.
_Avoid_: list, audience, segment, CSV

**Contact**:
One person on a contact list, with a phone number and the mapped values the call script needs.
_Avoid_: lead, prospect, record, row

**Column mapping**:
The link from a spreadsheet column to a **field id**. Because it targets ids rather than names, renaming a field can never orphan it.
_Avoid_: field mapping, header mapping

**Diallable**:
A contact the system may actually call — its number normalised to E.164, and every required input field carrying a value. Contacts failing either test are retained and marked with a reason, never silently dropped.
_Avoid_: valid, active, eligible

**Dedupe key**:
What decides whether two rows are the same call to the same person: the normalised number plus that agent's mapped input values. Two people sharing an office line are not duplicates, and neither are two appointments for one person.
_Avoid_: fingerprint, unique key, hash

**Acknowledgement**:
A one-time statement by a client account's owner that the people on their lists expect contact from their business and that they hold whatever permission their jurisdiction requires. Recorded once with the accepting user and a timestamp, and required before the first launch. There is deliberately **no per-contact consent field and no per-row consent check** — the obligation sits with the party who can actually attest to it.
_Avoid_: consent, opt-in, terms, agreement

**Import preview**:
Three rows of the uploaded list rendered exactly as the agent would speak them. The only place in the product where the client's own data meets the script, and therefore the only place a plausible-but-wrong column mapping becomes visible.
_Avoid_: dry run, sample, dry read

**Calling window**:
The hours during which an agent may dial, evaluated in the contact's local time.
_Avoid_: schedule, hours, timeframe

**Channel allocation**:
The slice of the account's simultaneous-call ceiling reserved for one client account. Zero-sum: allocations across all clients can never exceed the ceiling.
_Avoid_: concurrency, lines, seats, capacity

**Duration**:
How capacity is expressed to a client — "about 3 days for 640 contacts", never "4 channels". Channel allocation is agency vocabulary and does not appear on the client plane.

**Spend cap**:
The ceiling on what a single run may cost, in real settled money plus holds. Reaching it pauses the run and notifies the owner; nothing is lost. Per-run intent — the wallet is the account-wide hard gate.
_Avoid_: budget, limit, quota

**Pre-flight check**:
The set of conditions evaluated before a launch is allowed. A **blocking** condition refuses the launch; an **advisory** one warns and permits it.
_Avoid_: validation, readiness check, preconditions

**Pause**:
Suspending dialling with the run intact. Calls in flight complete, and resuming continues where it left off.

**Stop**:
Ending a run permanently. Undialled contacts stay undialled, and results and conversation history survive.
_Avoid_: cancel, abort, kill

**Schema freeze**:
The point at launch after which an agent's output fields can no longer be edited or re-scored. Changing them means duplicating the agent.

### Results

**Insight result**:
The value a single call produced for one output field.
_Avoid_: answer, extraction, output, data point

**Provenance**:
The link from an extracted value back to the phrase and moment in the transcript that produced it. Computed by Becca, not supplied by the telephony provider.
_Avoid_: source, citation, evidence, traceability

**Correction**:
A human-supplied value stored alongside an insight result, never overwriting it. Both values remain retrievable.
_Avoid_: edit, override, fix, amendment

**Saved view**:
A named combination of result filters. Any combination of filters is a valid view.
_Avoid_: report, segment, filter set

**Qualified**:
A saved view, not a fixed category or a field. What qualifies is defined per agent by whoever built it, and the system holds no opinion about it.
_Avoid_: qualified status, lead score, outcome

### Derived telephony objects

Becca's own database is authoritative for all agent configuration. The objects below are derived artifacts that can be rebuilt from it at any time.

**Scratch assistant**:
A throwaway telephony assistant giving test calls somewhere to run. Mutable, because it has exactly one consumer, and swept away after launch.
_Avoid_: sandbox, staging assistant, temp agent

**Run assistant**:
The immutable telephony assistant minted when an agent launches, holding a frozen snapshot of the agent and its output fields. Never mutated, and never cloned from another.
_Avoid_: production assistant, live agent, deployed assistant

### Billing

**Wallet**:
A client account's prepaid balance, in USD. Funded by bank transfer, credited by staff; every call draws from it. The ledger is the truth; the balance is a cached sum of it.
_Avoid_: account balance (ambiguous with Telnyx credit), credit line, deposit account

**Ledger entry**:
One append-only line in a wallet's history: a top-up, a call debit, a test-call debit, or an adjustment. Never edited, never deleted — corrections are new entries.
_Avoid_: transaction, record, row

**Top-up**:
Staff crediting a wallet after a client's bank transfer, with the transfer reference in the note.
_Avoid_: deposit, payment, recharge

**Rate**:
The flat per-minute price a client pays for every call, test calls included, rounded up to the next started minute. Set per client, shown plainly on the client plane, snapshotted onto each call when it dials.
_Avoid_: tariff, price plan, margin (that word means something else)

**Hold**:
The worst-case amount an in-flight call may cost (rate × the reconciliation timeout), counted against the wallet while the call is in the air. Computed from in-flight status, never stored; it dies with the call's status.
_Avoid_: reservation row, escrow, lock

**Adjustment**:
A staff-posted signed correction to a wallet, with a mandatory note. The append-only ledger's only instrument for fixing a mistake.
_Avoid_: edit, reversal, refund (an adjustment may be either direction)

**Margin**:
Becca's take: what the ledger billed minus what Telnyx charged, watched per client on the console's margin monitor. Agency vocabulary — never shown on the client plane. (Pre-wallet, this was a per-client percentage multiplier on invoices; that sense survives only in legacy receipts.)

**Billing entity**:
The legal name a client's pre-wallet invoices were issued to, which may differ from the client account name. Legacy — receipts only.
_Avoid_: company name, legal name
