# Call-quality scorecard — r1-scripted-baseline

Rubric v1 · 13 Aug 2026 10:47 UTC · 4 calls

## Violations per call

| call | annotation_leak | long_turn | ack_tic | verbatim_repeat | redundant_question | invention | context_loss | mishear_adoption | plan_deviation | closing_naturalness | total |
|---|---|---|---|---|---|---|---|---|---|---|---|
| test-4 | · | 3 | · | · | · | · | · | · | · | · | 3 |
| test-5 | · | 5 | · | · | · | · | · | · | 2 | 2 | 9 |
| test-6 | · | 2 | 1 | · | · | · | · | 1 | · | 1 | 5 |
| test-7 | · | 2 | · | · | · | · | · | · | 1 | 1 | 4 |
| **all** | · | **12** | **1** | · | · | · | · | **1** | **3** | **4** | **21** |

## test-4 — Buyer/Seller Intent Check

test_run · moonshotai/Kimi-K2.6 · 8 agent turns of 16

> The call largely followed the plan well: greeting, selling interest, buying interest, budget, and purchase-vs-rent/property type were all asked in order and captured naturally. The contact's mishear ("Spanish") was correctly glided past rather than adopted. One minor issue: the agent's closing line about "someone from the team will follow up" is a boundary-deflection phrase but was used here even though the contact had answered everything fully with no unanswered question pending — this is a slight misuse of that specific phrase, though not a major violation since it doesn't contradict the plan's close instructions. Overall a clean, warm, well-paced call with no significant redundant questions, invented details, or context loss.

- **long_turn** (turn 2): 3 sentences in one turn
  - "Hi Flourish! I'm calling on behalf of Silverstar — just a quick, friendly check-in on your interest in buying or selling property. Is now a good moment to chat?"
- **long_turn** (turn 13): 5 sentences in one turn
  - "Perfect, thank you Flourish! That's really helpful to know. Someone from our team will follow up with you personally about your plans. I truly appreciate your time today — the team values hearing about what you're looking for. Take care!"
- **long_turn** (turn 15): 3 sentences in one turn
  - "You're very welcome, Flourish! Have a wonderful day. Bye!"

## test-5 — Buyer/Seller Intent Check

test_run · moonshotai/Kimi-K2.6 · 10 agent turns of 19

> The agent skipped the selling-interest step until the contact pointed it out, and closed the call twice prematurely before the plan was actually complete, leading to an awkward, repeated-goodbye pattern. It also correctly handled the location correction at the end, but the overall flow was out of order and had two abrupt closes.

- **long_turn** (turn 2): 5 sentences in one turn
  - "Wonderful, Flourish! I'm calling on behalf of Silverstar — just checking in on your interest in buying or selling property. And it sounds like the timing really is perfect. You mentioned you're looking to rent a flat in Aba. May I ask what …"
- **plan_deviation** (turn 2): The plan requires asking about selling interest before buying interest; the agent skipped the selling-interest step entirely and jumped straight to buying/budget.
  - "Wonderful, Flourish! I'm calling on behalf of Silverstar — just checking in on your interest in buying or selling property. And it sounds like the timing really is perfect. You mentioned you're looking to rent a flat in Aba. May I ask what …"
- **long_turn** (turn 6): 5 sentences in one turn
  - "Understood, Flourish — renting it is. And a flat in Aba, noted. I really appreciate you taking the time to share your plans with me. The team will be in touch personally to follow up with you. Thank you so much, Flourish — truly valued spea…"
- **plan_deviation** (turn 6): Agent closed the call before covering the selling-interest step, which is required by the plan before closing.
  - "Understood, Flourish — renting it is. And a flat in Aba, noted. I really appreciate you taking the time to share your plans with me. The team will be in touch personally to follow up with you. Thank you so much, Flourish — truly valued spea…"
- **long_turn** (turn 10): 4 sentences in one turn
  - "Thank you for that, Flourish. So to confirm — renting a flat in Aba, budget around three million, and no selling interest at the moment. The team will follow up with you personally. Really appreciate your time today, Flourish — take care!"
- **closing_naturalness** (turn 10): This is a second full close after the plan was already closed once in turn 6, making the call feel repetitive and unnatural with redundant sign-offs.
  - "Thank you for that, Flourish. So to confirm — renting a flat in Aba, budget around three million, and no selling interest at the moment. The team will follow up with you personally. Really appreciate your time today, Flourish — take care!"
- **long_turn** (turn 14): 3 sentences in one turn
  - "I'm sorry, I didn't quite catch that, Flourish. But thank you again for your time today — the team will be in touch soon. Take care!"
- **closing_naturalness** (turn 14): Agent brushes off the contact's attempt to correct information and closes again, creating a third abrupt close within a few turns rather than engaging with the correction.
  - "I'm sorry, I didn't quite catch that, Flourish. But thank you again for your time today — the team will be in touch soon. Take care!"
- **long_turn** (turn 16): 5 sentences in one turn
  - "Got it, Flourish — thank you for clarifying! Yaba, not Aba. I'll make sure that's noted correctly for the team. Really appreciate you taking the time, Flourish — they'll follow up with you personally. Have a great day!"

## test-6 — Buyer/Seller Intent Check

test_run · moonshotai/Kimi-K2.6 · 10 agent turns of 20

> The agent generally followed the plan steps in order (opening, selling interest, buying interest, budget, property type) despite the contact's garbled, hard-to-parse responses. However, it adopted a nonsensical mishear ("Six slots") as if it were a valid property type answer without seeking clarification, and the close/final exchange included an extra unplanned turn after the goodbye, making the ending slightly drawn out rather than crisp.

- **long_turn** (turn 2): 3 sentences in one turn
  - "Hi Flourish! I'm calling on behalf of Silverstar — just a quick check-in on your interest in buying or selling property. Am I speaking with the right person?"
- **ack_tic** (turn 2): opened "hi flourish" same as the previous turn
  - "Hi Flourish! I'm calling on behalf of Silverstar — just a quick check-in on your interest in buying or selling property. Am I speaking with the right person?"
- **long_turn** (turn 16): 4 sentences in one turn
  - "Thanks for that, Flourish. I really appreciate you taking the time to share your plans with me today. The team truly values hearing about your plans, and someone will be in touch to follow up with you personally. Have a wonderful day!"
- **mishear_adoption** (turn 16): The agent accepted 'Six slots' (turn 15) as a valid answer to the property type question without any clarification, despite it being an obvious garbled/nonsensical transcription that doesn't correspond to any real property type.
  - "Thanks for that, Flourish. I really appreciate you taking the time to share your plans with me today."
- **closing_naturalness** (turn 18): The plan's close was already delivered in turn 16; this extra turn after the contact said goodbye adds an unplanned, slightly redundant tail-end exchange rather than ending cleanly once the plan was complete.
  - "You're very welcome, Flourish. Take care!"

## test-7 — Buyer/Seller Intent Check

test_run · moonshotai/Kimi-K2.6 · 8 agent turns of 18

> The call largely followed the plan structure (opening, selling interest, buying interest, boundaries on unanswerable questions, close), and the agent handled off-plan questions (property prices, ID number, commission) appropriately by deflecting to the team. However, there is a plan deviation in turn 13 where the agent both summarized the buying answer and asked an additional rent-specific follow-up in the same turn, and turn 17 adds an unplanned extra farewell line after the close was already delivered in turn 15, making the ending slightly repetitive.

- **long_turn** (turn 13): 3 sentences in one turn
  - "Understood — so no buying interest right now, and selling is a maybe for later. Thanks for clarifying, Flourish. Just to wrap up, are you looking to rent at all, or is it mainly just keeping an eye on selling for the time being?"
- **plan_deviation** (turn 13): The plan calls for a single buy-or-rent question with property type/budget follow-ups; here the agent packs a summary and an additional rent-specific question into one turn, going beyond the simple one-step-at-a-time buying interest ask.
  - "Understood — so no buying interest right now, and selling is a maybe for later. Thanks for clarifying, Flourish. Just to wrap up, are you looking to rent at all, or is it mainly just keeping an eye on selling for the time being?"
- **long_turn** (turn 15): 4 sentences in one turn
  - "I don't have commission details to hand, Flourish, but someone from the team will follow up with you personally on that. Thanks so much for your time today — I really appreciate it. The team truly values hearing about your plans, and we'll …"
- **closing_naturalness** (turn 17): The plan's close was already delivered in turn 15; this extra farewell turn is redundant and not part of the planned close, making the ending slightly tacked-on rather than a single clean close.
  - "You're welcome, Flourish. Have a great day!"
