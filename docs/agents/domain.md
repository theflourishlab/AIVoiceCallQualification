# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root — the glossary / ubiquitous language.
- **`docs/beccavoicefrd.md`** — the FRD: every functional requirement (`FR-*` ids), the data model, the build sequence, and the decision log in §18. Includes behaviours **tested against the live Telnyx account** that no public documentation states — treat those observations as fact, not as choices.
- **`docs/techstack.md`** — stack decisions, numbered `SD-nn`, each with status **Settled**, **Recommended**, or **Open**.
- **`docs/open-conflicts.md`** — known contradictions between the documents above that are not yet resolved. Check it before acting on either side of a conflict.
- **`docs/beccaproductui (14).html`** — the interactive product prototype: 22 annotated screens with the design tokens and per-screen intent notes. The visual and interaction reference.

If any of these files don't exist, proceed silently. Don't flag their absence.

## These documents are references, not law

The FRD and stack doc record the current best thinking — they are **not** beyond question, and the human working this repo does not want them treated as such.

**If you see a better approach than the documented one — especially a simpler one that looks like an oversight — do not silently implement your alternative, and do not silently follow the document either. Say something.** State the documented approach, your alternative, and why you think it's better, then let the human decide.

Two areas that need extra care — challengeable like everything else, but not by reasoning alone:

- **Tested facts about Telnyx** (marked "Tested" in the FRD, or stated as observed behaviour): verbatim voice ids, non-cascading assistant deletion, AMD defaults, recording defaults, URL expiry. These came from observing the live account, so armchair reasoning can't overturn them — but they can be stale or wrong. If you doubt one, say so and propose how to re-verify it (a live API check, the current OpenAPI spec), rather than either silently trusting it or silently assuming the opposite.
- **Anything in `docs/open-conflicts.md`**: don't pick a side and build on it; use the stated working assumption, flag that you did, and raise it if you think the assumption is wrong.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).
