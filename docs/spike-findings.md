# Phase 0 spike — findings

**Status: RUN against the live account, 6 August 2026.** One end-to-end
call to a real Nigerian mobile returned a structured insight result —
the Phase 0 gate is met. Raw evidence: the run transcript and
`spike_events.jsonl` (gitignored; summarised verbatim below).

Call: +2347001237041 → +2348031925030, answered 12:36:32Z, 29 seconds,
hangup by callee, `normal_clearing`. Insight scoring completed at
12:37:03Z — about two seconds after hangup. Results:
`{"still_attending": "yes"}` (schema insight) and a correct two-sentence
free-text summary.

## 1. Webhook envelopes — the open question, answered

Only **one** webhook was delivered for the whole call: a TeXML
`StatusCallback` (`CallbackSource: call-progress-events`,
`CallStatus: completed`).

- **Envelope: form-encoded, and SIGNED.** `content-type:
  application/x-www-form-urlencoded`, with valid
  `telnyx-signature-ed25519` + `telnyx-timestamp` headers over the raw
  body. The planned two-sub-route split (signed JSON vs unsigned form)
  is unnecessary: one verification story covers it. Ed25519 verification
  works on the raw form body exactly as on JSON.
- **Fields carried:** `CallSid`/`CallControlId` (same v3: value),
  `CallSessionId`, `CallLegId`, `CallStatus`, `CallDuration`,
  `AnsweredTime`/`StartTime`/`EndTime`, `HangupCause`, `HangupSource`,
  `From`, `To`, `ConnectionId`, `CallQualityStats` (stringified JSON).
- **No `conversation_id` appears in any callback.**
- **`call.conversation.ended` and `call.conversation_insights.generated`
  were NOT delivered** to the per-call `StatusCallback` URL. Their
  delivery target is configured elsewhere (candidates: the empty
  `webhook` field visible on the insights response; assistant-level
  settings; account-level webhook config). **Open for Phase 5** — until
  solved, results can be pulled: scoring completes seconds after
  hangup, so polling on the `completed` callback is a viable ingest
  path at our volumes.

## 2. Joining calls to conversations

- The dial response is minimal: `{"call_sid", "from", "to",
  "status": "queued"}` — no session id, no conversation id.
- The conversation's `metadata` (Telnyx-populated) carries
  `call_session_id`, `call_control_id`, `call_leg_id`, `assistant_id`,
  from/to. **The reliable join is `StatusCallback.CallSessionId` ↔
  `conversation.metadata.call_session_id`** (and `CallSid` ↔
  `metadata.call_control_id`).
- **Our `ConversationMetadata` dial parameter did NOT persist** — the
  conversation held only Telnyx-populated metadata. Either the param
  name/format is wrong or the ai_calls endpoint ignores it. FR-DISPATCH-5's
  our-ids-in-metadata mechanism is therefore **unproven**; the
  `call_session_id` join works without it and is what the `call` table
  should key on (it already does: `telnyx_call_session_id`). Re-test the
  metadata param before relying on FR-TEST-5's metadata tagging;
  fallback: tag test calls by assistant id, which is unique per agent.

## 3. Insight results

- `GET /ai/conversations/{id}/conversations-insights` (doubled plural
  confirmed) returns `data[].conversation_insights[]` of
  `{insight_id, result}`, plus `status: "completed"` and the
  `insight_group_id`. Schema-typed results come back as **stringified
  JSON** (`"{\"still_attending\": \"yes\"}"`); free-text insights as
  plain strings. Parse per-insight by whether the insight has a schema.
- Scoring latency: ~2 s after hangup.
- **Insight `json_schema` must set `"additionalProperties": false`** or
  creation 400s (error 10015). Fixed in `domain/serialize.py`.

## 4. Assistants and models

- **`POST /ai/assistants` requires a `model`, and `/ai/openai/models` is
  not the eligibility list**: `meta-llama/Meta-Llama-3.1-70B-Instruct`
  is listed there but 422s ("not available for AI Assistants",
  error 10027). `moonshotai/Kimi-K2.6` (what the account's existing
  assistants run) works. The assistant-eligible model list needs a
  better source — portal or support. Model choice is per-assistant
  config we own.
- **Create responses are unwrapped** — `POST /ai/assistants` returns the
  object directly, while list endpoints wrap in `data`. The insights
  create endpoints DO wrap in `data`. Parse tolerantly.
- `default_texml_app_id` confirmed at
  `telephony_settings.default_texml_app_id`; recording pin
  (`recording_settings.enabled: false`) accepted and visible on the
  created assistant; `privacy_settings.data_retention: true` accepted.
- Voice id `Telnyx.NaturalHD.astra` accepted verbatim (FR-AGENT-10 holds).

## 5. Dialling

- `POST /texml/ai_calls/{connection_id}` (the assistant's TeXML app id)
  **requires `AIAssistantId` in the payload** (10004 without it), even
  though the connection is the assistant's own.
- Full AMD block accepted (`MachineDetection: DetectMessageEnd`,
  `DetectionMode: Premium`, `AsyncAmd: true`,
  `MachineDetectionTimeout: 15000`, `AsyncAmdStatusCallback`).
  **No AMD callback was observed for this human-answered call** — and no
  `AnsweredBy` field appeared on the status callback. Whether an AMD
  result event fires (and its shape) is **still unverified**; needs a
  deliberate voicemail test call before Phase 4 relies on it.
- The agent spoke immediately on answer — no dead air (consistent with
  `AsyncAmd: true`).

## 6. Transcript

- `GET /ai/conversations/{id}/messages` returns `{role, text, ...}` but
  **not in chronological order** — the greeting appeared last in the
  list. Mirroring (FR-RESULT-1) must sort by the message timestamp
  field, not trust response order.

## 7. Object lifecycle (re-verified)

- Deleting the assistant → assistant 404s; **its TeXML application
  survives** and needs its own delete, which then succeeds
  (FR-LAUNCH-6 non-cascade reconfirmed live, twice).
- Insights and insight groups from the run (and from failed attempts)
  also survive independently; the sweeper should delete those too, or
  they accumulate. Orphans left on the account from this spike:
  insight groups `56812662-…`, `9a82df2e-…`, `8200f75e-…` and their six
  insights (harmless; conversation results are unaffected by group
  deletion per the scoring-once model, but verify before sweeping).

## 8. Cost

- Not yet read: detail records lag the call. Pull
  `GET /detail_records?filter[record_type]=ai-voice-assistant` (and
  `amd`) for 6 Aug 2026 once they land, to confirm the AMD line item
  and per-call cost shape (FR-BILL-2/4).
- **Read live 12 Aug 2026 — see §11.** The FRD's assumed join key was
  wrong for two record types; tts carries no cost at all; inference
  carries no join at all.

## 9. Unreachable numbers (tested 12 Aug 2026)

Deliberate call to a known-unreachable Nigerian mobile
(`+2347034640951`), placed through the test screen with the full AMD
block and live callbacks (fresh quick tunnel, signature verified).

- Exactly **one** status callback arrived: `CallStatus: failed`,
  `SipHangupCause: 480`, `CallDuration: 0`, `HangupSource: unknown`,
  `SequenceNumber: 0`. No `ringing`/`initiated` event preceded it.
- **No AMD callback** — expected, the call never answered. Combined
  with §5 (no AMD callback on a human answer either), an AMD result
  event has now NEVER been observed in any outcome. The machine-answer
  case is still untested (no voicemail-enabled number available), but
  the design stance must be: `answered_by` is optional enrichment; the
  dispatcher must be fully correct when no AMD verdict ever arrives.
- **SIP 480 is ambiguous.** A subscriber who is unreachable presents
  identically to a terminating carrier rejecting the caller ID (§5's
  0700 finding). The dispatcher cannot tell them apart from the hangup
  cause alone. Consequences: 480 must be retryable (unreachable now is
  not unreachable tonight), and a bad caller ID would therefore burn a
  retry cycle across an entire list — the FR-LAUNCH-2 pre-flight
  caller-ID check is the real protection, not the retry policy.

## 10. First real end-to-end dispatch run (12 Aug 2026)

Four contacts, allocation 1, launched from the UI, dialled by the
worker against real Telnyx. All four completed (62s/42s/66s/82s);
callbacks drove the whole queue lifecycle; the drain check marked the
run finished with no manual help.

- **`CallStatus: conversation_ended`** exists — an extra TeXML callback
  arriving just before `completed`, carrying no CallSid context we use.
  Ignored safely by ingest; documented so Phase 5 knows it is not an
  error.
- **AMD, final word: still no event.** Every dial carried the full
  FR-DISPATCH-4 block with AsyncAmdStatusCallback set, all four calls
  were answered by humans, and `AnsweredBy` arrived on nothing. The
  optional-enrichment stance is not a hedge; it is the observed
  behaviour of the platform.
- **Empty balance presents as account disablement**, not a payment
  error: 403 code 10010 "Account is disabled D17" on the dial, while
  every CRUD endpoint (assistants, insights, groups) keeps working —
  so a launch succeeds and then every dial fails. Dispatcher follow-up
  owed: classify account-level 403s as run-level (pause the run)
  instead of burning each contact's retry budget.
- A launch that dies this way leaves its run assistant orphaned at
  Telnyx once the run is reset; the sweeper only covers scratch
  assistants today.

## 11. Detail records read live (12 Aug 2026, FR-BILL-2/4)

All shapes below are from `GET /v2/detail_records?filter[record_type]=…`
against the real account (54 ai-voice-assistant, 13 amd, 490 inference,
1525 tts, 55 recording, 32 noise-suppression, 0 stt, 0 media_storage).

- **The join key is NOT uniformly `call_session_id`** as FRD §12
  assumed. Per type: `ai-voice-assistant` and `recording` carry
  **`telnyx_session_id`**; `amd` and `noise-suppression` carry
  **`call_session_id`**. Both hold the same UUID our status callbacks
  deliver (verified: all four §10 calls' `telnyx_call_session_id`
  values match live records exactly, e.g. `01d1e732-…` → cost 0.1,
  billed_sec 120).
- ~~`tts` records carry no cost~~ **CORRECTED same day (§11a): only
  conversational tts records lack cost** (bundled into the per-minute
  AI rate — $0.05/min, `rate_measured_in: ai_voice_assistant_minutes`,
  billed per started minute: 61s → 120 billed_sec). Standalone tts
  usage (voice previews, 1–6 Aug) IS costed: $1.01 over 1,525 records.
- **`inference` records (insight generation) join to NOTHING**: their
  `conversation_id` is null in practice, there is no session/leg id,
  and `billing_group_id` is null. They are real, billable
  (`billable: true`), tiny (~$0.0005/insight run,
  `meta-llama/Llama-3.3-70B-Instruct`, `usecase: insight-generation`),
  and attributable to a client only by inference-time heuristics we do
  not have. Cost sync therefore treats them as **account-level
  overhead**, not per-call cost; FR-BILL-9's buffered estimates absorb
  them on the client side.
- `billing_group_id` on pre-group records is null/empty and will stay
  so (historical snapshots, §FR-BILL-3 confirmed live) — the 12 Aug
  E2E run's costs are permanently unattributed. Group created and
  number attached the same day, so everything dialled after is covered.
- `amd` line items are real and separately billed: $0.0065 per call
  with `is_telnyx_billable: true` (FR-BILL-4 confirmed).

Consequence: `call.cost_actual` = sum of per-session costs across
`ai-voice-assistant` + `amd` + `recording` + `noise-suppression`
(+ `media_storage` if it ever appears), joined on
`coalesce(telnyx_session_id, call_session_id)`.

## 11a. The PSTN leg, and what a call REALLY costs (12 Aug 2026)

§11's record-type list undercounted true cost ~4x. Caught the same day
reconciling the live balance: after a $10 top-up the user expected
$9.64, saw $8.40, and the §11 types explained only ~$0.40 of the $1.24
delta. The July Telnyx invoice PDF (`GET /v2/invoices/{id}?action=link`
→ presigned `download_url`; the summary + LEDGER section is the ground
truth detail_records only approximate) closed the gap:

- **The PSTN termination leg is a separate record type,
  `sip-trunking`** — $0.117/min to Nigerian mobiles
  (`GLOBAL-CONV-RATE0-USAGE`), joined on `telnyx_session_id`. It is
  the LARGEST per-call component, 2.3x the conversational-AI rate.
  `call-control` legs add $0.002/min each. Both verified present for
  all five 12 Aug calls; the reconciliation then balances to the cent:
  $0.819 sip-trunking + $0.35 AI + $0.014 call-control + $0.026 amd
  + ~$0.04 ns/recording/inference ≈ $1.25 ≈ the observed $1.24.
- **Number MRC is $35.00/month PER NUMBER** (`NG-NATIONAL-RATE0-MRC`,
  3 × $35 = $105/mo, debited the 1st) — it never appears in
  detail_records, only on the monthly invoice, and at current volume
  it dwarfs usage. Client invoicing must carry each client's number
  MRC explicitly or Becca eats it.
- The assistant's own LLM tokens are billed as `inference` records
  (`INFERENCE-API-KIMI-K25-INFERENCE-MODEL-INPUT`, ~$0.10 per heavy
  test day) — still joinable to nothing (§11), still account overhead.
- `page[size]` on detail_records is **silently capped at 50**;
  `meta.total_pages` honestly reflects the cap, so page loops that
  trust it stay correct. `filter[started_at][gte]` works (verified)
  for future windowed syncs.
- Rule of thumb going forward: a 2-minute Nigerian mobile call costs
  **~$0.37 metered** (~$0.185/min all-in); FR-BILL-9 quotes high, so
  the flat estimate is set to $0.45/call.
- **A `recording` line bills on every AI call even with `Record=False`**
  ($0.002/min — verified on a deliberate cost-check call dialled 12 Aug
  with the flag off, and present on all prior calls). The client's
  recording toggle governs OUR per-call flag and playback, not this
  cost line; it appears to be assistant-conversation storage on
  Telnyx's side. Include it in every cost model.
- Verified end-to-end 12 Aug (call to a fresh allowlisted number,
  2 billed minutes): detail records totalled **$0.3485**, matching the
  model to the cent. The BALANCE debited only the AI-minutes component
  immediately ($0.10); the remaining components posted later — the
  balance is eventually-consistent, detail records are the settled
  per-call truth.

## Consequences for the build plan

1. Webhook handler: single route, single Ed25519 verification, parse
   form-encoded bodies. No JSON/form split.
2. Results ingest (Phase 5): design for **poll-on-completed** as the
   baseline; upgrade to push if the insights webhook target proves
   configurable. FR-RESULT-1's trigger event may not be deliverable as
   the FRD assumed.
3. `call` table joins on `telnyx_call_session_id`, populated from the
   status callback — not from the dial response, which carries only
   `call_sid`.
4. Re-verify the conversation-metadata mechanism before building
   FR-TEST-5 (test-call tagging) on it.
5. AMD behaviour on voicemail: machine-answer case still unobserved
   (no voicemail-enabled number; unreachable → SIP 480 with no AMD
   event, see §9). Phase 4 designs `answered_by` as optional
   enrichment — dispatch correctness must not depend on an AMD
   callback that has never been seen to fire.
6. Sweeper scope grows: assistant + TeXML app + insights + group.
