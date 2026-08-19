"""The import preview: rows rendered exactly as the agent would speak them.

The only place in the product where the client's own data meets the
script (FR-CONTACT-7), and therefore the only place a plausible-but-wrong
column mapping becomes visible. Costs nothing and places no call.
"""

from becca.domain.model import AgentVersionContent, FieldRef


def speak_row(content: AgentVersionContent, values_by_field_id: dict[int, str]) -> str:
    """Render the script with one contact's mapped values substituted.

    A missing value renders as a visible gap marker rather than silently
    vanishing — the preview's whole job is making problems visible.
    """
    parts: list[str] = []
    for block in content.script_blocks:
        if isinstance(block, FieldRef):
            value = values_by_field_id.get(block.field_id)
            if value is None or not value.strip():
                key = content.field_by_id(block.field_id).key
                parts.append(f"⟨missing {key}⟩")
            else:
                parts.append(value)
        else:
            parts.append(block.content)
    return "".join(parts)
