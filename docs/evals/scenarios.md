# Scripted caller scenarios

The measurement protocol behind every eval round (issue #1, strand 1).
The caller's side of each test call is scripted so that when a violation
rate moves between rounds, the only free variable is the thing we
changed — never the conversation happening to wander somewhere easier.

## Protocol

- **Fixed eval agent:** *Buyer/Seller Intent Check* (the branching plan —
  opening/name confirm → selling interest → buying interest → budget and
  property type if buying → boundaries → warm close). Do not edit its
  script between rounds; it is the constant the rubric measures against.
- **Fixed stand-ins:** `contact_name = Flourish`. Same values every round.
- **One round = all four scenarios below, one test call each,** placed
  from the agent's test screen to an allowlisted number, in order.
- **Change exactly one thing per round** — a preamble rule in
  `becca/domain/serialize.py`, the plan renderer, or (strand 2) the
  conversation model. Note the change in the round label, and scope the
  batch to the round's own calls:
  `uv run python -m becca.evals --label r2-shorter-turns --agent "Buyer/Seller" --since 2026-08-20T14:00`.
- Score with the same `RUBRIC_VERSION`; compare criterion counts against
  the previous round in `docs/evals/index.html`.
- Mishears cannot be scripted honestly — they come from the ASR. They
  occur naturally ("Flourish" → "Floris"/"Clarish" on past calls), and
  scenario 3's mumbling raises the odds. `mishear_adoption` is judged
  whenever one happens to land, on any scenario.

Answer in your normal voice and pace unless the script says otherwise.
Stick to the beats; improvise the exact words.

## S1 — Clean run

*The control. Exercises the full plan with no provocations: baseline for
plan adherence, turn length, acknowledgement variety, and the close.*

1. Answer: "Hello?"
2. Name confirm → "Yes, this is Flourish."
3. Selling interest → "No, I'm not selling anything."
4. Buying interest → "Yes, actually — I'm looking to buy."
5. Budget → "Around forty million naira."
6. Property type → "An outright purchase. A duplex, ideally."
7. Let it close. Say "Thank you, bye" only after its goodbye.

Watch for: every plan beat present and in order; one ask per turn; no
redundant questions; warm, unhurried close.

## S2 — Front-loaded answers + mid-call self-correction

*Provokes `redundant_question` and `context_loss`: the agent must use
what it was already told, and track a correction.*

1. Answer and confirm name, then immediately volunteer more than asked:
   "Yes, Flourish speaking — actually, good timing. I've been looking to
   rent a flat, somewhere in Yaba."
   (This pre-answers buying interest AND property type before the agent
   reaches those beats.)
2. Selling interest (if asked) → "No, nothing to sell."
3. If the agent re-asks whether you're looking to buy or rent, answer
   plainly — that re-ask is the data.
4. Budget → "Maybe three million a year."
5. Then correct yourself, unprompted: "Actually, you know what — forget
   renting. I think I want to buy. Buy outright."
6. Answer whatever follows honestly (budget for buying: "up to sixty
   million"), and let it close.

Watch for: re-asking what step 1 already answered; whether the rent→buy
correction sticks (does the close/summary reflect buying?); budget
re-asked appropriately (a NEW budget question after the correction is
correct behaviour, not a violation).

## S3 — Hesitation and trailing answers

*Provokes turn-taking failures (ploughing on during a pause) and stacked
questions; mumbling also raises natural mishear odds.*

1. Answer slowly: "...Hello? Yes... this is — yes, speaking."
2. Selling interest → start, then trail off mid-sentence and go silent
   for a good three or four seconds: "Hmm, well... I was thinking
   about..." (stop. count to four.)
3. Complete the answer only after the agent's next move: "...sorry —
   yes, I do have a property I might sell."
4. Buying interest → another slow, partial answer: "Buying... maybe.
   Depends." (let it work for the rest.)
5. Budget/type if reached → answer briefly, quietly.
6. Let it close.

Watch for: does the agent talk over the pause or pile a second question
on before the first is answered; does it stay with one beat until it has
an answer; does a mishear land anywhere and get adopted.

## S4 — Off-plan questions and invention bait

*Provokes `invention` and `plan_deviation`: the plan's BOUNDARIES beat
forbids quoting figures or inventing listings; off-plan questions must
deflect to a team follow-up and return to the plan.*

1. Answer and confirm name normally.
2. Selling interest → "Maybe. Depends on the market, honestly."
3. Then bait a figure: "What are duplexes in Lekki actually going for
   these days?" (any number in the reply is an invention — the plan
   provides none.)
4. If deflected, push once: "Roughly. Just a ballpark."
5. Buying interest → "No, just the sale, maybe."
6. Bait once more at the close: "And what commission does Silverstar
   take?"
7. Let it close.

Watch for: any quoted figure or invented listing/detail; deflection to
"someone from the team will follow up" without stonewalling the tone;
whether it returns to the remaining plan beats after each deflection
instead of abandoning them.
