# Call-quality scorecard — r4-haiku-name-rule

Rubric v1 · 13 Aug 2026 13:30 UTC · 1 calls

## Violations per call

| call | annotation_leak | long_turn | ack_tic | verbatim_repeat | redundant_question | invention | context_loss | mishear_adoption | plan_deviation | closing_naturalness | total |
|---|---|---|---|---|---|---|---|---|---|---|---|
| test-14 | · | 3 | · | · | · | · | · | · | 1 | · | 4 |
| **all** | · | **3** | · | · | · | · | · | · | **1** | · | **4** |

## test-14 — Buyer/Seller Intent Check

test_run · anthropic/claude-haiku-4-5 · 10 agent turns of 20

> The call generally followed the plan well: greeting, selling interest, buying interest, budget, and property type were all covered in order with natural phrasing, and the close warmly thanked the contact by name. The main issue is at turn 11, where the agent combined two separate plan asks (rental-vs-purchase and property type) into one turn, and skipped the "rental or outright purchase" question entirely since the contact had already said "rent" — though the plan wanted this explicitly asked. This is a minor plan deviation but not severe.

- **long_turn** (turn 1): 3 sentences in one turn
  - "Wonderful! Flourish, this is a call on behalf of Silverstar — I'm just reaching out to check in on whether you might be interested in buying or selling property. Is now an okay time to chat briefly?"
- **plan_deviation** (turn 11): Packs multiple questions into one turn and skips explicitly asking whether they want to rent or purchase outright, conflating it with property type/style questions instead of following the plan's two distinct asks.
  - "And what kind of property are you looking for — like a flat, a house, something else? And are you open to different types, or do you have something specific in mind?"
- **long_turn** (turn 15): 3 sentences in one turn
  - "Lovely! A house it is. Are you open to different styles of houses, or is there anything specific you're looking for?"
- **long_turn** (turn 17): 5 sentences in one turn
  - "I'm here! So you're open to different kinds of houses — that's great. Thank you so much for sharing all of this with me, Flourish. I really appreciate your time, and the team will absolutely follow up with you personally about what we have …"
