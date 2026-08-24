# Functional Requirements Document
## Becca — AI Voice Call Qualification

**Version** 1.0 · **Date** 4 August 2026 · **Status** Ready for build
**Telnyx OpenAPI spec** v2.24.0, verified 4 August 2026, with six behaviours tested against the live account

---

## 1. Purpose and scope

A web application where a business owner describes a phone call in plain English, and the system builds a voice agent that places that call to an imported contact list and returns structured answers.

Becca operates it as an agency. One Telnyx account serves several client businesses. Clients never see each other, never see cost, and never touch infrastructure.

### 1.1 In scope for v1

Agent creation from a plain-English brief · test calling · contact import and column mapping · scheduling and dispatch · live monitoring · structured result extraction · CSV export · client account administration · usage-based invoicing.

> **Amended 14 Aug 2026 (A1):** "usage-based invoicing" is superseded — clients prepay a **wallet** billed at a flat per-minute rate; invoices survive only as pre-wallet receipts.

### 1.2 Out of scope for v1

Inbound calls · **live transfer to a human** · mobile layouts · agent template library · CRM integration · payment gateway · self-service sign-up · SMS channel · multi-language agents · owning recording retention.

> Transfer was specified in an earlier draft because the Telnyx API offers it, not because the product needs it. It is cut. An SMB running outbound reminders has nobody sitting by a phone waiting to receive a warm transfer, and the script already handles the case properly by promising a callback: *"if they ask about pricing, say a consultant will follow up."* Add it when a client asks for it and has a staffed line to receive it.

### 1.3 Glossary

| Term | Meaning |
|---|---|
| **Client account** | One client business. The tenant boundary. Was called "workspace" in earlier drafts. |
| **Agent** | The thing a user builds *and* the thing that dials. Carries a status. Runs once. |
| **Run** | An agent's single dialling lifecycle. Not a separate object. Was called "campaign". |
| **Field set** | The single ordered list of an agent's fields. The one artifact. |
| **Field** | One entry in the field set. `kind: input` is supplied by the contact list; `kind: output` is extracted from the conversation. |
| **Variable contract** | A *view*: `fields where kind = input`. Not a separate object. |
| **Insight schema** | A *view*: `fields where kind = output`. Frozen at launch. Not a separate object. |
| **Scratch assistant** | A throwaway Telnyx assistant created so test calls have somewhere to run. |
| **Run assistant** | The immutable Telnyx assistant minted when an agent launches. |
| **Becca console** | The agency-only surface spanning all client accounts. |

---

## 2. Architecture

### 2.1 Two planes

**FR-ARCH-1.** The system presents two distinct surfaces. They are separate applications sharing a database, not one application with role-based hiding.

**Becca console** — one surface spanning every client. Reachable only by Becca staff. Holds cost, margin, invoices, number inventory, channel allocation and Telnyx account health.

**Client account** — scoped to exactly one client business. Holds their people, agents, contacts, results and what they owe. Must never reveal that another client exists.

> **Amended 14 Aug 2026 (A1):** the console now holds **wallet balances and per-minute rates**; margin survives only as an internal billed-vs-cost monitor; invoices are frozen receipts. The client account holds a wallet rather than "what they owe."

**FR-ARCH-2.** Becca staff may *enter* a client account and operate inside it, since Becca builds agents on clients' behalf. While entered, a persistent banner states which account they are in, and every action is attributed to the Becca user in the audit log.

**FR-ARCH-3.** A client session requesting any console endpoint receives 403.

### 2.2 Three layers of Telnyx object

| Layer | Holds | Lifetime |
|---|---|---|
| Our database | Agent configuration. **Source of truth.** | Permanent |
| Scratch assistant | Somewhere for test calls to run | Created on first test, swept after launch |
| Run assistant | Frozen snapshot of the agent and its schema | Created at launch, never mutated |

**FR-ARCH-4.** Our database is authoritative for all agent configuration. Telnyx objects are derived artifacts and may be rebuilt from our records at any time.

---

## 3. Authentication and users

**FR-AUTH-1.** There is no sign-up. Google OAuth authenticates only; it never creates an account. An authenticated email with no matching membership receives a refusal directing them to their Becca contact.

**FR-AUTH-2.** The first Becca staff account is seeded at deploy as configuration, not through any screen.

**FR-AUTH-3.** Becca staff are managed from the console as a list of Google email addresses. No invitation is sent; the person signs in and it works.

**FR-AUTH-4.** Becca staff have a single role with full access: every client account, all cost and margin, channel and number allocation, and the ability to add or remove client users.

**FR-AUTH-5.** Client users are added by Becca on request. There is no invitation flow, no tokens, no expiry, no pending state, no accept page.

**FR-AUTH-6.** Client users hold one of two roles:

| Role | May |
|---|---|
| **Owner** | Everything a member can do, plus launch, pause and stop agents, and see the bill |
| **Member** | Build agents, run test calls, import contacts, read and export results |

**FR-AUTH-7.** Launching is the only permission that separates the roles, because it is the only action that spends money and calls real people.

> **Amended 14 Aug 2026 (A1):** since wallet billing, test calls also spend money (FR-WALLET-5), and members deliberately keep them — testing is the build loop. The rationale is now that launching is the only action that **dials real contacts at scale**. The permission split itself is unchanged.

**FR-AUTH-8.** The client-facing Team screen is read-only. It shows who has access and what each may do, with instruction to contact Becca to change it.

---

## 4. Agent creation

### 4.1 The brief

**FR-AGENT-1.** The user describes the call in free text. A single textarea, not a form.

**FR-AGENT-2.** An agent version stores **one list of fields** and **one script**. Nothing else about the agent's content is stored, because everything else is derived from those two.

A **field** is one named thing the agent either needs or captures:

- `kind: input` — a value the contact list must supply, such as `first_name` or `visit_date`
- `kind: output` — a value captured from the conversation, such as `budget_band`

The **script** is not a block of text. It is a sequence of pieces. A piece is either literal words, or a pointer to a field.

### Why this matters: a field's name is written in exactly one place

Consider renaming `first_name` to `given_name`.

**If the script were text** it would contain the characters `{{first_name}}`, the contract would contain the word `first_name`, and the CSV mapping would contain it too. The name is written in three places, so a rename means three edits, and missing one produces an agent that runs and quietly speaks nonsense.

**Because the script holds a pointer**, the name exists only on the field itself. The script says "field 1 goes here", not "first_name goes here".

```
fields:
  1  key="first_name"  kind=input   required
  2  key="visit_date"  kind=input   required
  3  key="budget_band" kind=output  enum[under_50m, 50_80m, 80_120m]

script_blocks:
  text      "Good afternoon, is this "
  field_ref → 1                        ← a pointer, not the word
  text      "? You are booked for "
  field_ref → 2
```

Renaming field 1 changes one value. The script still points at field 1 and needs no edit. The CSV mapping still targets field 1 and needs no edit. There is nothing to keep in sync, because nothing else ever held the name.

### The contract and the schema are questions, not documents

They are not stored anywhere. They are asked of the field list:

| View | Is literally | Used for |
|---|---|---|
| Variable contract | `fields where kind = input` | Which columns the CSV must have |
| Insight schema | `fields where kind = output` | What gets extracted after the call |

Because they are computed from the field list every time they are needed, they cannot disagree with it. There is no copy to fall out of date.

**FR-AGENT-3.** The failures below are **unrepresentable**. Not caught, not validated against. There is no way to write them down.

| Old failure | Why it cannot occur |
|---|---|
| Script names a variable the contract lacks | A `field_ref` points at a field id. There is no way to write a reference to a field that does not exist. |
| Contract holds an entry the script never uses | An input field is created by inserting it into the script and removed when its last reference goes. An unreferenced input field cannot exist. |
| Renaming breaks the script or the CSV mapping | A rename writes `fields[].key`. Every reference is by id and is untouched. |
| A name is both an input and an output | They are one list with unique keys. The collision has nowhere to exist. |
| A schema edit desynchronises from the mapping | Column mapping targets field ids, so a rename cannot orphan it. |

> An earlier draft specified validating three separate artifacts against each other at four boundaries. That was a control, and a control is only as good as the developer who remembers to call it. Pointing at a field instead of naming it removes the failure class rather than policing it.

**FR-AGENT-3A.** Exactly one validation survives, at exactly one place: **the boundary where model output becomes a domain object.** A `field_ref` whose `field_id` is not in the emitted field set is a generation failure. Retry; never surface it.

> This is the only point in the system where an incoherent structure can be *proposed*, because it is the only point where the structure arrives from outside. Everything downstream operates on the domain object, where the invalid state has no representation.

**FR-AGENT-3B.** Users never type mustache syntax. The two kinds of field are managed differently, and neither has a delete-refused state.

**Input fields are created and removed by editing the script.** Inserting one is a single gesture: type `/` or press Insert field, and a menu lists the existing fields plus "New field". Choosing "New field" names it, adds it to the field set, and places the chip, all at once. Removing the last chip that references a field removes the field.

> So an input field exists precisely because the script uses it. It cannot be orphaned, because there is no way to create one that is not referenced, and no way to keep one that is not. The input list on screen 04 is a read-only view of what the script needs.

**Output fields are a plain editable list.** Add, rename and remove freely. Nothing references them, so nothing can break.

**FR-AGENT-3B1.** If a user types or pastes `{{something}}` into the script out of habit, the editor detects it and converts it to a chip, creating the field if it does not exist. It is never left as literal text.

> Mustache syntax exists only in the string we render for Telnyx at launch. It is a serialisation detail and never appears in the interface.

**FR-AGENT-3D.** What this model does **not** protect against, stated plainly so nobody assumes otherwise:

| Remaining failure | Mitigation |
|---|---|
| A field is named and referenced correctly but means the wrong thing. `visit_date` is mapped to a column that holds the date the visit was *booked*. | The import preview renders three real rows as the agent would speak them (FR-CONTACT-7). |
| An output field's enum values do not match how people actually answer. | The test loop, before the schema freezes (FR-TEST-1). |
| A contact row is missing a value. | FR-CONTACT-9, caught at import. |

> The model removes structural incoherence. It cannot remove being wrong about the world. Both remaining risks are semantic, and both already have a screen designed to catch them.

**FR-AGENT-3C.** Serialising to Telnyx is a **pure function** of the field set and the script blocks:

- `script_blocks` → the mustache `instructions` string
- `fields where kind = input` → `dynamic_variables` keys
- `fields where kind = output` → the insight JSON schema

Because the input is coherent by construction, the output cannot be otherwise. Nothing in the serialiser validates; it only renders.

**FR-AGENT-4.** Generation uses structured output, tool-use enforced, and emits the field set and the script blocks as **one object**. The model never emits three things that have to agree, because it is never asked to.

### 4.2 Review and edit

**FR-AGENT-5.** All three views appear on one screen. In the script, a field appears as a chip.

**FR-AGENT-5A.** A chip behaves as **one indivisible character**. The cursor sits before it or after it, never inside. Backspace removes the whole chip in one press. There is no way to edit half of it, because there is no text inside it to edit.

| | If the chip were decorated text | As specified |
|---|---|---|
| Cursor can go inside | Yes | No |
| Backspace once | Deletes one character, leaving `{{first_nam` | Removes the whole chip |
| Result of a stray keystroke | A broken reference that still looks almost right | Nothing. The keystroke lands beside the chip as ordinary text |

**FR-AGENT-5B.** The script editor is a block editor, not a textarea. A user inserts a field by picking from a menu. **There is no way to type a field reference.** Malformed input never reaches the system because the interface cannot express it.

> The prototype already drew variables as chips. That was a visual instinct which turns out to be the correct data model.

**FR-AGENT-6.** Renaming a field writes one column, `fields[].key`. Nothing else is touched, because nothing else stores the name. There is no propagation step, and therefore no partial rename.

**FR-AGENT-7.** Output fields prefer enumerated values over free text wherever the brief implies a closed set. Only typed fields are filterable and exportable as columns.

**FR-AGENT-8.** Editing produces a new agent version. Versioning is ours; Telnyx `versions` and `canary-deploys` endpoints are not used.

### 4.3 Voice and call behaviour

**FR-AGENT-9.** The following are configurable per agent and map to these Telnyx fields:

| Control | Field | Notes |
|---|---|---|
| Voice | `voice_settings.voice` | See FR-AGENT-10 |
| Speaking speed | `voice_settings.voice_speed` | 0.25–2.0 |
| Transcription model | `transcription.model` | 12-value enum; default `deepgram/flux` for accented English |
| Caller may interrupt | `interruption_settings.enable` | Default true |
| Keep transcripts | `privacy_settings.data_retention` | **Must be true or no insights run** |
| Hard call limit | `telephony_settings.time_limit_secs` | 30–14400 |
| Hang up after silence | `telephony_settings.user_idle_timeout_secs` | |
| Voicemail behaviour | `telephony_settings.voicemail_detection.on_voicemail_detected.action` | See FR-DISPATCH-4 |

**FR-AGENT-10.** Voice identifiers must be used **verbatim** from `GET /v2/text-to-speech/voices`. They must never be reassembled from `provider`, `model_id` and `name`.

> Tested. All 4,471 IDs are fully qualified, e.g. `Telnyx.NaturalHD.astra`. Sending `NaturalHD.astra` returns 400; sending `astra` returns 500. Segment counts vary from two to four because a model ID may itself contain a dot, and provider casing is irregular.

**FR-AGENT-11.** Voice preview synthesises a fixed sample sentence via `POST /v2/text-to-speech/speech`, proxied through our backend so the Telnyx key never reaches the browser. Because the sample text is constant and Telnyx caches by default, repeat previews are cache hits. Preview speed must be clamped to 0.5–2.0, narrower than the assistant's 0.25–2.0.

**FR-AGENT-12.** `telephony_settings.recording_settings.enabled` is set to **false** on every assistant the system creates. Recording is controlled per call (see FR-REC-2).

> The Telnyx default is `true`. Leaving it unset records everything.

---

## 5. Testing

**FR-TEST-1.** Test calling is a loop, not a checkpoint. The insight schema is editable directly on the test screen and each edit applies to the next test call.

**FR-TEST-2.** Each test places a real, billed call. The UI displays a running test count and test spend, and the per-call price on the action button.

> There is no way to re-score a transcript at Telnyx, so the only truthful test of a schema is the path production takes.

**FR-TEST-3.** The system maintains one **scratch assistant** per agent, created on first test. Schema edits mutate its insight group directly:

- Add a field → `POST /ai/conversations/insights` then assign to the group
- Change a field → `PUT /ai/conversations/insights/{id}`
- Remove a field → unassign

Safe because the scratch assistant has exactly one consumer.

**FR-TEST-4.** Each test run stores a **snapshot of the schema** used, because insight results carry no template revision. Only our record can say which schema produced which result.

**FR-TEST-5.** Test conversations are tagged in `conversation_metadata` so they never appear in run results.

**FR-TEST-6.** An agent may not launch until it has been test called at least once.

**FR-TEST-7.** The test screen shows a diff of which extracted fields changed since the previous test.

**FR-TEST-8.** Test calls use stand-in values typed by the user. They shape the script; verifying the contact list is the import preview's job (FR-CONTACT-7).

**FR-TEST-9.** When a test call sounds wrong, the user describes what was wrong in plain language on the same screen and regenerates. The regeneration is scoped to the described problem and preserves the field set unless the description requires changing it.

> "It read the date as numbers" and "it used her full name in the greeting" are both fixable in the script. Keeping that loop inside the test screen means the user never leaves the place where they can immediately hear whether the fix worked.

---

## 6. Contacts

**FR-CONTACT-1.** A contact list belongs to the agent it was mapped for. The agent must be selected before a file can be uploaded, because the agent determines the required columns.

**FR-CONTACT-2.** Accepted formats: CSV, XLSX. Parsing, header detection and normalisation happen server side.

**FR-CONTACT-3.** Phone numbers are normalised to E.164. Rows that cannot be parsed are retained in the list, marked, and excluded from dialling.

**FR-CONTACT-4.** Uploaded columns are mapped to the agent's **input fields, by field id**. Mapping is auto-suggested by name similarity and confirmed by the user. Because the mapping stores ids, renaming a field later cannot orphan it.

> A user never has to rename a field to match their spreadsheet. That is what mapping is for: a column headed "Full Name" maps to field 1 regardless of what field 1 is called. Renaming inputs is for readability only. Renaming **outputs** does matter, because an output field's name becomes the column header in the results table and the CSV export.

**FR-CONTACT-5.** An unmapped **required input field** is a blocking error. The user may either map a column or make the field optional. Removing it entirely is refused while the script references it, per FR-AGENT-3B.

**FR-CONTACT-6.** There is **no per-contact consent field and no per-row consent check.** A consent column in a spreadsheet cannot be verified, would block legitimate lists whose consent is recorded elsewhere, and adds import complexity for no protection.

Responsibility sits at the account level instead, per FR-CONSOLE-2A.

> DND registry checking is also not implemented. The NCC administers it via short code 2442 with no public API. No screen claims either control exists.

**FR-CONTACT-7.** The import screen previews **three rows** as the agent would speak them: the first, one from the middle, and one chosen at random. Values render exactly as the agent would voice them. Costs nothing and places no call.

> This is where the consequences of a column mapping become visible. It is the only place in the product where the user's own data meets the script, so it is the only place these can surface:

| In the file | Mapped to | The agent would say |
|---|---|---|
| `Mr. Adewale Ogunbiyi` | `first_name` | "Good afternoon, is this Mr. Adewale Ogunbiyi?" |
| `2026-08-07` | `visit_date` | "two thousand and twenty six, dash, zero eight..." |
| `11:30:00` | `visit_time` | "eleven thirty and zero seconds" |
| A "Booking Date" column holding when they *booked* | `visit_date` | A confidently wrong date, to every contact |

The last one is the dangerous case. The mapping is valid, the format is clean, the test call passed. Nothing structural detects it. A human reading one sentence does.

> Three rows rather than one, because row 1 is often the tidiest row in the file.

**FR-CONTACT-8.** A contact list has a unique constraint on `(contact_list_id, dedupe_key)`, where `dedupe_key` is a hash of the normalised E.164 number **plus that agent's mapped input values**. On import the first occurrence wins and later ones drop, joining the existing exclusion count with reason `duplicate`.

The key adapts to whatever fields the agent uses, so it works on any client's data shape without configuration.

| Two rows | Same number | Same mapped values | Outcome |
|---|---|---|---|
| The same row pasted twice | Yes | Yes | Second dropped. Correct: it is the same call to the same person. |
| Two people on one office line, different `first_name` | Yes | No | Both kept. Correct: a shared line is not a duplicate person. |
| One person with two bookings, different `visit_date` | Yes | No | Both kept. Correct: two appointments need two reminders. |
| `0803 000 1188` and `+2348030001188` | Yes, after normalisation | Yes | Second dropped. Normalisation runs before the key is computed. |

> Deduping on the phone number alone would silently delete the second person on a shared office line, and the second of two appointments. Both are ordinary in Nigerian SMB contact data. Including the mapped values costs nothing extra, since it is the same single index, and it makes the rule mean what it should: *the same call to the same person* is a duplicate, *a different call to the same person* is not.

**Not attempted:** fuzzy matching. "Chidinma" and "Chidinma A." on one number will produce two calls. Detecting that reliably is not worth the false positives it would create.

**FR-CONTACT-9.** Per-row completeness is checked at import, not discovered at dial time:

- A contact missing a value for a **required** variable is retained, marked not diallable with reason `missing_required_value`, and excluded from the launch count.
- A contact missing a value for an **optional** variable resolves to that variable's default from `dynamic_variables` on the assistant. Every optional variable must therefore carry a default, and the default must read naturally when spoken, for example "a consultant" rather than an empty string.

> Schema coherence is an agent-level property. This is a row-level one, and they fail differently. A perfectly coherent agent still breaks on row 4,000 if that row has an empty cell.

---

## 7. Launch

**FR-LAUNCH-1.** Launch requires a tested agent, a mapped contact list, a schedule and a spend cap.

**FR-LAUNCH-2.** A pre-flight check runs before launch and again server side at launch, so a stale browser cannot bypass it. Blocking failures refuse the launch; advisories warn only.

Blocking conditions: client account acknowledgement not accepted (FR-CONSOLE-2A) · agent never test called · required variables unmapped · caller ID not verified or not in-country · destination country not enabled on the outbound voice profile · `data_retention` false · no channels allocated · no number assigned.

Advisory conditions: estimated duration given channel allocation · schema freeze notice.

**FR-LAUNCH-3.** Launch sequence, executed transactionally with compensation on failure:

1. Mirror test transcripts and results into our database
2. `POST /ai/conversations/insights` — one per schema field, a snapshot at this instant
3. `POST /ai/conversations/insight-groups` — a new group owned by this run alone
4. Assign each insight to the group
5. `POST /ai/assistants` — built from our stored config, pointing at the new group, with `recording_settings.enabled: false` **always** (see note below)
6. `PATCH /v2/phone_numbers/{id}` — point `connection_id` at the new assistant's `default_texml_app_id`
7. `POST /api/agents/{id}/queue` — populate **our** dispatch queue
8. Mark the scratch assistant orphaned
9. Set agent status to scheduled or dialling

> **On step 5.** `false` here does not mean recording is off. It means **the assistant does not decide**. Telnyx defaults this field to `true`, so leaving it unset would record every call regardless of what the client wants. Pinning it to `false` hands the decision to the per-call `Record` flag, which the dispatcher reads from the client's current setting at dial time (FR-REC-2).
>
> This is what makes the client-wide toggle *live* rather than frozen. Assistant config is immutable once a run starts, so a client switching recording off mid-run could not take effect if the setting lived there. On the per-call flag it applies to the very next call.

**FR-LAUNCH-4.** The Telnyx `clone` endpoint must **not** be used. A clone copies `insight_settings.insight_group_id`, sharing the group with the original, which is exactly the corruption this design prevents.

**FR-LAUNCH-5.** Scratch assistant deletion is **not** part of the launch sequence. A background sweeper handles it, so a cleanup failure can never abort a launch.

**FR-LAUNCH-6.** The sweeper must delete **both** the assistant and its auto-created TeXML application.

> Tested. Deleting an assistant does not cascade. Conversations survive with messages intact, and the TeXML application and any bound storage credential survive as orphans returning 200 after the assistant returns 404. Deleting only the assistant accumulates one dangling connection per test cycle.

**FR-LAUNCH-7.** Launching freezes the insight schema for the life of the run. It cannot be edited or re-scored afterwards. To change it, the user duplicates the agent.

---

## 8. Dispatch

**FR-DISPATCH-1.** The system owns the dispatch queue. Telnyx `scheduled_events` is **not** used.

> Three independent reasons. `ScheduledCallSettings` exposes only `sip_region`, so it cannot enable answering machine detection. It has no pause, so pausing would mean deleting and recreating every pending event. And per-client channel allocation must be enforced by us regardless. Any one of these forces us to hold the queue; together they make the wrapper more work than the queue.

**FR-DISPATCH-2.** A worker dispatches calls respecting: the calling window in the contact's local time, excluded time bands, the client's channel allocation, the retry policy, and the spend cap.

**FR-DISPATCH-3.** Calls are placed via `POST /v2/texml/ai_calls/{connection_id}` where `connection_id` is the run assistant's `default_texml_app_id`.

**FR-DISPATCH-4.** Every outbound call must send:

```
MachineDetection:        "DetectMessageEnd"
DetectionMode:           "Premium"
AsyncAmd:                true
MachineDetectionTimeout: tuned below the 30000 default
AsyncAmdStatusCallback:  our webhook
```

> `telephony_settings.voicemail_detection` on the assistant does nothing unless AMD is enabled at dial time. All AMD defaults are off, so silence means the agent holds full conversations with answerphones. `AsyncAmd: true` lets the agent start speaking immediately so humans hear no dead air; `DetectMessageEnd` waits for the greeting so a message lands after the beep.

**FR-DISPATCH-5.** Per-call payload carries `AIAssistantDynamicVariables` (the contact's mapped values), `conversation_metadata` (our agent, run and contact IDs as **strings**), `Record` (see FR-REC-2), `StatusCallback` and `Timeout`.

> Conversation metadata is string-typed while the event input accepts integers. Send strings to avoid coercion.

**FR-DISPATCH-6.** Pause sets a flag the worker reads. Nothing is sent to Telnyx. Calls in flight complete. Resume clears the flag.

**FR-DISPATCH-7.** Stop drains the queue and marks the run finished. Undialled contacts remain undialled. The run assistant is retained so results and conversation history survive.

**FR-DISPATCH-8.** Retry policy is configurable per run: number of attempts and interval. Retries respect the calling window.

**FR-DISPATCH-9.** The concurrency governor must never exceed the client's channel allocation, and the sum of all allocations must never exceed the account ceiling. It is enforced **in the database**, inside the same transaction that locks the queue row, by counting that client's calls currently in `dialling` state.

> Not an in-process counter. Every managed platform overlaps old and new during a deploy, so two workers can briefly exist. An in-memory governor would then permit double the allocation, and Telnyx would allow it. A count in the same transaction as the row lock is correct regardless of how many workers are running.

> Telnyx enforces one account-wide ceiling and knows nothing about per-client splits. Without our governor, one client starves another and Telnyx will not intervene.

**FR-DISPATCH-10.** Reaching the spend cap pauses the run and notifies the owner. Nothing is lost.

**FR-DISPATCH-11.** Before placing a call, the worker asserts that the rendered script contains no unresolved `{{`. This is an **invariant assertion, not a control.** Given FR-AGENT-3 it cannot fail. If it ever does, the call is not placed, the run pauses, and Becca staff are paged, because a firing assertion means the domain model has been violated somewhere upstream and the correct response is to stop rather than to skip a row.

---

## 9. Results

**FR-RESULT-1.** On `call.conversation_insights.generated`, the system stores the insight results **and** fetches and stores the full transcript via `GET /ai/conversations/{id}/messages`.

> There is no documented retention window for `/ai/conversations`. Mirroring makes our database the system of record and removes the dependency.

**FR-RESULT-2.** The results table renders each insight schema field as a real column. Filters are generated from the run's frozen schema, so the filter bar differs per agent and is always correct.

**FR-RESULT-3.** Any combination of filters is a valid view. Views may be saved. "Qualified" is a saved view, not a fixed category.

**FR-RESULT-4.** Results are available while the run is still dialling.

**FR-RESULT-5.** CSV export reflects the current filtered view, with schema fields and contact fields as columns.

**FR-RESULT-6.** The call detail screen shows the transcript with **every extracted value linked to the moment that produced it**, including the quoted phrase and timestamp.

> Telnyx provides no provenance. This linkage is computed by us. Without it, the first wrong extraction destroys trust in the whole dataset.

**FR-RESULT-7.** A user may correct an extracted value. The correction is stored **alongside** the original, never overwriting it.

> There is no endpoint to update an insight result at Telnyx, so our copy and theirs diverge from that point. Both values must be retrievable.

**FR-RESULT-8.** Previous and next navigation on call detail stays scoped to the filter set the user arrived from.

---

## 10. Recording

**FR-REC-1.** Recording is a **client account** setting, owned by the client. It is not per agent and not per call from the user's perspective.

**FR-REC-2.** Implementation: assistant-level `recording_settings.enabled` is pinned to `false`; the per-call `Record` flag on `ai_calls` is set from the client's current setting at dial time.

> Because we own the dispatcher, switching recording off takes effect on the very next call, including mid-run. An assistant-level setting could not do this.

**FR-REC-3.** The system stores the **recording ID**, never a download URL.

> Tested. Signatures are minted at request time. A 48-minute-old recording returned a fresh working URL while the prior URL expired at exactly 600 seconds. Lazy playback is therefore reliable and no time-bounded fetch worker is required.

**FR-REC-4.** Playback and export mint a fresh URL per request via `GET /v2/recordings/{id}`.

**FR-REC-5.** There is **no bulk recording export** in v1. A single recording is played or downloaded from the call detail screen, which covers the real need.

> Evaluated and cut. The build is a background job that enumerates every recording, mints a fresh URL for each because they expire at 600 seconds, downloads perhaps two gigabytes, zips it, stores the archive, emails a link, and handles failing halfway through. A day of work plus storage plus a job that fails quietly at 3am.
>
> Against that, the realistic demand is thin. A dispute needs **one** recording. An audit request needs **a few**. Migrating away happens once, and Becca can run a script. Per-call playback already serves every one of those, and it is already built.
>
> If a client ever needs everything at once, that is a support task for an agency, not a product feature.

**FR-REC-6.** Becca does **not** own recording retention in v1. Recordings remain in the telephony provider's storage. The product states plainly that:

1. Recordings are held by our telephony provider, not by Becca
2. How long they are kept, and how quickly they become available, is set by that provider
3. Individual recordings can be played or downloaded from any call, and Becca will retrieve a wider set on request

**FR-REC-7.** Turning recording off does not delete recordings already made. The UI says so.

---

## 11. Becca console

**FR-CONSOLE-1.** Client list showing people count, channel allocation, assigned number, calls month to date, amount to bill and status, with an action to enter the account.

**FR-CONSOLE-2.** Creating a client account captures name, billing entity, margin percentage and the first owner's email. No Telnyx object is created at this point.

> **Amended 14 Aug 2026 (A1):** the margin percentage is vestigial — still captured, kept only for legacy invoice math. The live pricing knob is the per-minute **rate** (default $0.30), edited on the console's Billing & wallets screen.

**FR-CONSOLE-2A.** Before a client account can launch its first agent, its owner accepts a one-time acknowledgement: that the people on their lists are expecting contact from their business, and that they hold whatever permission their jurisdiction requires. Recorded once with the accepting user and a timestamp. Never shown again.

> This is where the obligation actually belongs. Becca processes calls; the client owns the relationship with the people being called and the lawful basis for calling them. One checkbox at setup is both lighter than a per-row check and more defensible than one, because it is an explicit statement by the party who can actually make it.

**FR-CONSOLE-3.** Channel allocation is zero-sum against the account ceiling. The console shows the split across all clients and the unallocated remainder.

**FR-CONSOLE-4.** A client cannot launch until channels and a number are both assigned. The console surfaces this as an onboarding checklist.

**FR-CONSOLE-5.** Number inventory shows every number, its assigned client, its use and its status, with actions to order, assign and reassign. Reassignment is refused while a run is dialling on that number.

**FR-CONSOLE-6.** Account health surfaces: verification tier, enabled destinations, regulatory document expiry, and Telnyx balance with a projected exhaustion date.

**FR-CONSOLE-7.** A zero Telnyx balance stops every client simultaneously. Becca staff must be alerted well before projected exhaustion.

**FR-CONSOLE-8.** All console actions are written to an audit log, including entering a client account.

---

## 12. Billing

> **⚠ SUPERSEDED IN PART — 14 Aug 2026.** The post-paid invoice model
> below was replaced by a **prepaid wallet with a flat per-minute
> rate**. Read **Amendment A1 (§12-W)** at the end of this document
> before acting on anything in this section. Surviving unchanged:
> FR-BILL-2/3/4 (cost retrieval, now Becca's internal margin monitor).
> Superseded: FR-BILL-1/5/6(part)/7/9. **Inverted:** FR-BILL-8.

**FR-BILL-1.** Invoices are computed as **actual metered Telnyx cost × (1 + margin)**.

> This makes unknown Telnyx line items self-correcting. Insight pricing is confirmed to exist but unpublished; billing from actuals means it lands in the cost figure automatically and margin holds regardless.

**FR-BILL-2.** Cost is retrieved from `GET /v2/detail_records`, filtered by `record_type`. Relevant types: `ai-voice-assistant`, `amd`, `inference`, `tts`, `stt`, `recording`, `noise-suppression`, `media_storage`. Records carry `rate`, `rate_measured_in`, `cost`, `currency` and `is_telnyx_billable`, and join to our calls on `call_session_id`.

**FR-BILL-3.** One Telnyx **billing group** per client account, attached to that client's phone numbers, so cost attribution returns natively on the detail record.

> `billing_group_id` is confirmed present on all `ai-voice-assistant` records. It is currently null because no billing groups exist. Detail records are historical snapshots and will not backfill, so groups must be created before the first billed run.

**FR-BILL-4.** AMD is separately billable and must be included in the cost model, since it is enabled on every call.

**FR-BILL-5.** Margin is set per client and may differ between clients.

**FR-BILL-6.** The client-facing billing view shows amount owed, outstanding invoices, invoice history, a per-agent split of the billed total, and payment details. It must **not** show Telnyx cost, margin, or minute counts.

> Minutes plus a public rate card would let a client derive the margin.

**FR-BILL-7.** Invoices are generated per client per period, delivered as PDF, and paid by bank transfer. Payment is marked manually. There is no payment gateway.

**FR-BILL-8.** A late invoice never suspends a running agent.

**FR-BILL-9.** Pre-launch spend estimates carry a buffer for unpublished line items and are quoted slightly high.

---

## 13. Notifications

**FR-NOTIFY-1.** Notifications are **in-app only**. No email, no transactional email provider, no domain verification, no deliverability to manage.

> This deletes the last blocking open item and a whole class of infrastructure. It costs a table, an unread count and a panel.

**FR-NOTIFY-2.** The honest consequence: a user discovers an event when they next open the app, not when it happens.

That is acceptable here because **every client-facing failure is fail-safe**:

| Event | What the system does | Cost of finding out late |
|---|---|---|
| Agent stops dialling | Stops. No calls placed. | Lost time, no money burned |
| Spend cap reached | Pauses. No calls placed. | Lost time, no money burned |
| Import has blocking errors | Refuses launch. The user is already in the app doing the import. | None |
| Agent finishes | Results are ready and waiting | None |

Nothing continues going wrong while unread. Delayed discovery costs dialling hours, never money or a bad call.

**FR-NOTIFY-2A.** The exception is Becca-side. **A zero Telnyx balance stops every client at once**, and unlike the client failures it is not fail-safe from the clients' point of view: their agents simply stop and they will not know why. Balance and its projected exhaustion date are therefore shown persistently on the console landing screen rather than as a dismissible notification.

**FR-NOTIFY-2B.** For v1 the real backstop is human. Becca operates this as a done-for-you agency with one client, watches the console, and calls the client when something halts. The notification centre records what happened; the agency is what responds to it.

| Event | Audience |
|---|---|
| Agent stops dialling | Client |
| Spend cap reached | Client |
| Import has blocking errors | Client |
| Agent finishes, results ready | Client |
| Channel pool exhausted | Becca |
| Telnyx balance low | Becca, and persistent on the console |
| Port completed, verification lapsing | Becca |

Removed as meaningless without email: the daily 18:00 summary, which in-app is just the dashboard, and per-call notifications, which were never viable. **Quiet hours are also removed**, since an unread badge does not wake anyone at night.

**FR-NOTIFY-3.** Preferences are stored per user, not per client account. Each event is on or off.

---

## 14. Data model

```
becca_staff        id, google_email, created_at
client_account     id, name, billing_entity, margin_pct, channel_allocation,
                   recording_enabled, telnyx_billing_group_id, status,
                   acknowledged_at, acknowledged_by_user_id
user               id, client_account_id, google_email, role(owner|member), last_seen_at
agent              id, client_account_id, name, status(draft|tested|scheduled|
                   dialling|finished|halted), current_version_id,
                   telnyx_scratch_assistant_id, telnyx_scratch_texml_app_id,
                   telnyx_run_assistant_id, telnyx_run_texml_app_id,
                   telnyx_insight_group_id, duplicated_from_agent_id
agent_version      id, agent_id, n, fields(jsonb), script_blocks(jsonb),
                   voice_settings(jsonb), telephony_settings(jsonb), created_at
                   -- fields[]       {id, key, kind(input|output), required,
                   --                 type, values[], instructions}
                   -- script_blocks[] {type(text|field_ref), content|field_id}
                   -- NO separate variable_contract or insight_schema columns.
                   -- Both are views over fields[]. Storing them would
                   -- reintroduce the drift this model removes.
test_run           id, agent_id, agent_version_id, schema_snapshot(jsonb),
                   telnyx_conversation_id, transcript(jsonb), results(jsonb),
                   cost, created_at
contact_list       id, client_account_id, agent_id, filename, row_count,
                   diallable_count, column_mapping(jsonb), source_file(bytea)
contact            id, contact_list_id, phone_e164, variables(jsonb),
                   dedupe_key, diallable, exclusion_reason
                   -- UNIQUE (contact_list_id, dedupe_key)
                   -- dedupe_key = hash(phone_e164 + normalised mapped values)
run_schedule       agent_id, window_start, window_end, days, excluded_bands(jsonb),
                   timezone, retry_attempts, retry_interval_secs, spend_cap,
                   not_before, paused
queue_item         id, agent_id, contact_id, state(pending|dialling|done|failed|
                   skipped), attempts, last_attempt_at, next_attempt_at
call               id, agent_id, contact_id, telnyx_call_session_id,
                   telnyx_call_control_id, telnyx_conversation_id,
                   telnyx_recording_id, answered_by, status, duration_sec,
                   cost_actual, started_at, ended_at
transcript         call_id, messages(jsonb), fetched_at
insight_result     id, call_id, field_key, value, corrected_value, corrected_by,
                   corrected_at, source_quote, source_timestamp_sec
invoice            id, client_account_id, period, telnyx_cost, margin_pct,
                   amount, status(draft|sent|paid|overdue), pdf(bytea)
audit_log          id, actor_type(staff|user), actor_id, client_account_id,
                   action, target, metadata(jsonb), created_at
```

**FR-DATA-1.** `telnyx_scratch_texml_app_id` and `telnyx_run_texml_app_id` are stored explicitly because deleting an assistant orphans its TeXML application (FR-LAUNCH-6).

**FR-DATA-2.** `insight_result.corrected_value` is separate from `value` (FR-RESULT-7).

**FR-DATA-4.** `contact_list.column_mapping` maps a spreadsheet column to a **field id**, never to a field name. A rename therefore cannot orphan a mapping.

**FR-DATA-5.** `insight_result.field_key` stores the field **id** alongside the key, so results remain attributable after a rename in a later version.

> **Amended 14 Aug 2026 (A1):** the schema above predates the wallet. Migration 0011 adds the append-only `wallet_ledger` table plus `client_account.rate_per_min_usd` / `wallet_balance_usd` and rate snapshots on `call` / `test_run`; the `invoice` table survives as frozen history and gains no new rows.

**FR-DATA-6.** The only binary data the system stores is the uploaded spreadsheet and the generated invoice PDF, both as `bytea`. Together these are single-digit megabytes per year. There is no object storage tier, because recordings stay with the telephony provider (FR-REC-6).

**FR-DATA-3.** All identifiers sent to Telnyx as `conversation_metadata` must be strings.

---

## 15. Non-functional

**FR-NF-1.** Concurrency ceiling is 10 simultaneous calls, treated as fixed. It is not requestable by clients and is allocated by Becca.

**FR-NF-2.** Capacity is presented to clients as duration, not channel count. "About 3 days for 640 contacts", not "4 channels".

**FR-NF-3.** Every generated agent greeting must identify the caller and state the purpose, per the NCC Consumer Code.

**FR-NF-4.** Calls to Nigerian numbers must present a Nigerian caller ID. International CLI is rejected by Telnyx and by terminating carriers.

**FR-NF-5.** Webhook signatures must be verified (Ed25519 via `telnyx-signature-ed25519` and `telnyx-timestamp`).

**FR-NF-6.** The Telnyx API key is never exposed to the browser. All Telnyx traffic is proxied through our backend.

**FR-NF-6A.** No transcript, phone number, contact name or uploaded file content may leave our infrastructure through an error report, log aggregator or analytics tool. Scrubbing is configured before the first event is ever sent, because captured data cannot be recalled.

> Error trackers capture request bodies and local variables by default. In this system that means an unhandled exception in the insights webhook would transmit a recorded conversation verbatim to a third-party vendor.

**FR-NF-7.** Desktop-first. Minimum supported width 1280px.

**FR-NF-8.** Design conforms to Becca Design Language v3: Bricolage Grotesque for display, Geist for body, Geist Mono for labels and metadata; cyan as the only functional accent; magenta rationed to failure states and one data-viz series; hairlines rather than shadows; colour never the sole carrier of meaning; WCAG AA.

---

## 16. Build sequence

| Phase | Contains | Gate |
|---|---|---|
| **0. Spike** | Create assistant, insight group, place one call with AMD, receive webhooks, read insights | One end-to-end call with a structured result |
| **1. Spine** | Auth, client accounts, agent CRUD, generation, review screen | An agent can be created and reviewed |
| **2. Test loop** | Scratch assistant, schema mutation, test call, result display, describe-and-regenerate | Schema can be iterated against real calls |
| **3. Contacts** | Import, normalise, map, validate | A list validates against a contract |
| **4. Dispatch** | Queue, worker, governor, AMD, pause, retry | An agent dials a list end to end |
| **5. Results** | Webhook ingest, transcript mirror, table, filters, export | Results are filterable and exportable |
| **6. Console** | Clients, allocation, numbers, staff | Becca can onboard a client unaided |
| **7. Billing** | Billing groups, detail records, invoices | An invoice reconciles against Telnyx |
| **8. Notifications** | Notification table, unread count, panel, preferences | Events are recorded and visible |

**FR-BUILD-1.** Telnyx Level 2 verification and Nigerian number provisioning must begin before Phase 0, as both have external lead times.

**FR-BUILD-2.** Billing groups must exist before the first billed run (FR-BILL-3).

**FR-BUILD-3.** No transactional email provider is required anywhere in v1, no object storage tier, and no job scheduler. Housekeeping runs inside the dispatcher's existing loop, and invoice generation is a button on the console. The periodic scheduler still exists for the orphan sweeper, digests, invoicing and cost sync (see stack decisions), but nothing in the recording path needs it.

---

## 17. Open items

| # | Item | Blocking | Owner |
|---|---|---|---|
| 1 | Insight per-unit pricing unpublished. Non-blocking: billing from actuals absorbs it. | No | Telnyx |
| 2 | Conversation retention window undocumented. Non-blocking: transcript mirroring removes the dependency. | No | Telnyx |
| 3 | Whether BYO storage receives assistant recording bytes. Deferred: Becca does not own retention in v1. | No | Deferred |
| 4 | Nigeria per-minute rate not published. Needed for the cost model. | Phase 7 | Telnyx |

---

## 18. Decision log

| Decision | Rationale |
|---|---|
| Agent is the run | One agent, one list, one dialling lifecycle. Makes the per-run frozen schema automatic rather than enforced. |
| One Telnyx assistant per run | `insight_settings` is assistant-level, so a shared assistant would break the schema freeze. |
| Never use the clone endpoint | A clone shares the insight group with its source. |
| We own the dispatcher | Scheduled events cannot set AMD, cannot pause, and cannot enforce per-client allocation. |
| Schema frozen at launch | Telnyx scores each call once with no re-run, and results carry no template revision. |
| No invitation flow | Roughly five access events a year per client. Becca provisions, matching the phone-number pattern. |
| One Becca staff role | A permissions layer with a single setting is a bug waiting to happen. |
| Recording is client-owned | It is their consent decision. Provider holds the bytes; Becca does not own retention. |
| No per-row consent check, one account-level acknowledgement | A consent column cannot be verified and blocks good lists. The obligation belongs to the client, who can actually attest to it. |
| Import preview stays; test calls use typed values | The preview is free and is the only place the user's own data meets the script. Paying for a call to learn the same thing is a worse trade. |
| Bill from actuals | Unknown Telnyx line items self-correct and margin holds. |
| One artifact, three views | Three artifacts can disagree. One cannot. The failure mode is removed rather than policed. |
| Script references fields by id | A rename is one column write with no propagation, so a partial rename cannot exist. |
| Script editor is a block editor | A user cannot type a reference to a field that does not exist, because typing references is not possible. Mustache syntax never appears in the interface. |
| Input fields are managed from the script, not from a list | Removes the orphaned-field state and the delete-refused dialog entirely. A field exists because the script uses it. |

---

*Supporting research in the project: `telnyx-feasibility-assessment`, `insight-mutability-verification`, `telnyx-object-lifecycle`, `account-architecture`, `dispatch-decision`, `agent-is-the-campaign`, `api-surface-flags`, `live-account-test-results`. Interactive UI with the full API registry: `becca-product-ui.html`, 22 screens, 231 controls, 144 mapped calls.*
---

## Amendment log

Amendments append; the original text above is never rewritten, only marked. Anyone acting on a marked section must read its amendment first.

| # | Date | Scope | Summary |
|---|------|-------|---------|
| A1 | 2026-08-14 | §12 Billing | Post-paid invoices replaced by a prepaid wallet + flat per-minute billing. FR-WALLET-1..8 below. |
| A2 | 2026-08-14 | Terminology | Client-facing "call script" renamed **"call guide"** — behavioural direction, not verbatim lines. Code identifiers unchanged. |
| A3 | 2026-08-14 | Client UI | Overview built; Review, Test and Contacts screens redesigned. The shipped templates now supersede the 22-screen prototype (`beccaproductui (14).html`) for these screens — see §A3. |

---

## A1 — §12-W. Wallet billing (14 Aug 2026)

Decided 14 Aug 2026 (planning session; grilled and settled decision-by-decision — record: `docs/adr/0001-prepaid-wallet-ledger.md`). Replaces the §12 post-paid model. Rationale: cash-flow inversion (the client pays first; Becca stops fronting Telnyx cost and carrying collection risk), price legibility (one public number instead of an invisible margin), and prepaid being the native Nigerian model.

**FR-WALLET-1.** Each client account has a prepaid **wallet**, denominated USD. The append-only `wallet_ledger` is the source of truth; the balance column on the account is a cache maintained transactionally with every ledger write. Funding is by bank transfer, credited manually by Becca staff in the console with a note carrying the transfer reference. There is no payment gateway.

**FR-WALLET-2.** Calls are billed at a **flat per-client rate per minute** (default $0.30/min), **charged per second** — rate × seconds ÷ 60, half-up to the cent, so 61 seconds bills $0.31 at $0.30/min. *(Amended 24 Aug 2026; originally rounded up to the next started minute.)* A completed call with zero duration — or one whose charge rounds to $0.00 — bills nothing; failed/busy/no-answer calls bill nothing. The rate and the per-second rule are stated plainly on the client's wallet screen; ledger lines still show started-minute counts for legibility.

**FR-WALLET-3.** The rate is **snapshotted onto each call attempt when it is claimed**, and settlement uses the snapshot: no call ever bills at a rate that was not in force when it dialled. Staff rate changes notify the client on actual change — silent repricing is forbidden.

**FR-WALLET-4.** **Reserve-then-settle.** Every in-flight attempt holds `rate × MAX_CALL_MINUTES` (default 15 min, matching the reconciliation timeouts). A dial is only claimed if the balance covers all current holds plus one more; settlement debits actuals on the completed callback, idempotently (ledger unique index per call). Settlement of actuals may take the balance negative; a negative balance blocks all new dialling until topped up.

**FR-WALLET-5.** **Test calls are billed like any call** — same rate, same per-second charge — and are attributed in the ledger. (Supersedes their silent Becca-absorption; the test screen's "billed like any call" is now true.) *Amended 24 Aug 2026:* a test call is gated by the **balance itself** — it may be placed while the wallet holds at least `WALLET_FLOOR_USD` (default $1) — rather than by FR-WALLET-4's worst-case hold, which stays the rule for run dispatch.

**FR-WALLET-6.** **Insufficient balance pauses runs** — the deliberate inversion of FR-BILL-8. The run pauses with a client notification; nothing is lost; topping up and resuming continues where it left off. A capacity-relative low-balance warning (two dialling waves of holds) fires before the hard stop. Launch pre-flight blocks when the wallet cannot cover a single call and advises when it cannot cover the whole list.

**FR-WALLET-7.** **Becca absorbs number rental** ($35/number/month MRC). Clients pay per-minute and nothing else — no monthly fee, no platform fee. The console's margin monitor accounts for MRC internally (per-client margin = ledger-billed − Telnyx cost − numbers held × MRC).

**FR-WALLET-8.** The client wallet screen shows the balance, the rate, the round-up rule, top-up instructions, and **every ledger line item** (per-call, per-test-call, top-ups, adjustments — including minute counts, deliberately superseding FR-BILL-6's minute-hiding: with a public flat rate, minutes derive nothing secret). Telnyx cost and margin remain console-only. Pre-wallet invoices stay readable as receipts on both planes; cost sync (FR-BILL-2/3/4) survives as Becca's internal margin monitor. Corrections are staff-posted signed **adjustments** with a mandatory note — the ledger itself is never edited.

---

## A2 — Terminology: "call script" is now "call guide" (14 Aug 2026)

Client-facing wording only. The artifact FR-AGENT-2 defines is unchanged; "script" wrongly implied verbatim lines when the artifact is behavioural direction (the same insight that removed FR-CONTACT-7). Everywhere this document says "script", the product now says **guide**. Code identifiers (`script_blocks`, `ScriptBlock`) keep the historical name; "instructions" stays reserved for the derived rendering sent to the telephony assistant. Authority: CONTEXT.md § Building an agent.

---

## A3 — Client UI: shipped screens supersede the prototype (14 Aug 2026)

The 22-screen interactive prototype (`beccaproductui (14).html`) remains the design-system reference, but for the screens below the SHIPPED TEMPLATES are now authoritative — each was redesigned through in-browser variant review on a kept `prototype/*` branch (the branch tip carries a VERDICT commit recording what was chosen and why):

- **Overview (screen 01)** — built at `/overview` (nav item 1; `/` stays the agents list). KPIs: Calls today / Reached (= completed with talk time, so reached ⇔ billed) / Results captured / Spend today (wallet ledger). Deliberately absent vs the prototype: the "Qualified" KPI (per-agent saved view; the system holds no opinion), the voicemail outcome slice (AMD has never fired live), and "top failure" prose (failure causes are not stored). "Today" = midnight Africa/Lagos. Branch `prototype/overview`.
- **Review (screen 04)** — guided numbered sections in a centred column (1 the call guide, 2 fields the list must supply, 3 what gets extracted in a scrolling card), sticky save bar. Branch `prototype/agent-review`.
- **Test (screen 06)** — guided sections reading as the loop (1 run a test, 2 what came back with separated value rows, 3 tune and go again). Branch `prototype/agent-test`.
- **Contacts pick/map (screens 07/08)** — pick: compact agent rows, full width; map: health-first (verdict strip → blocker fixes → mapping → details). Branches `prototype/contacts-pick`, `prototype/contacts-map`.
- **Billing (screens 16/19)** — superseded by the wallet model itself (A1), not a layout revamp: the client screen is now **Wallet** at `/wallet` (balance, rate stated plainly, per-call ledger, receipts) and the console screen is **Billing & wallets** (credits, rates, adjustments, margin monitor). The prototype's amount-owed / generate-invoices / margin-editor screens no longer exist.

Remaining screens still tracking the prototype until their own revamp: Describe (03), Voice (05), Schedule & launch (09).
