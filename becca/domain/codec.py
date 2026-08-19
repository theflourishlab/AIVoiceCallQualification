"""JSON codec for the one artifact — storage and generation both use it.

Decoding runs the constructor, so it IS the FR-AGENT-3A boundary: a
dangling field_ref or duplicate key raises and never becomes a domain
object.
"""

from typing import Any

from becca.domain.model import (
    AgentVersionContent,
    Field,
    FieldKind,
    FieldRef,
    FieldType,
    ScriptBlock,
    TextBlock,
)


def content_to_json(content: AgentVersionContent) -> dict[str, Any]:
    return {
        "fields": [
            {
                "id": f.id,
                "key": f.key,
                "kind": f.kind,
                "required": f.required,
                "type": f.type,
                "values": list(f.values),
                "instructions": f.instructions,
                "default": f.default,
            }
            for f in content.fields
        ],
        "script_blocks": [
            {"type": "text", "content": b.content}
            if isinstance(b, TextBlock)
            else {"type": "field_ref", "field_id": b.field_id}
            for b in content.script_blocks
        ],
    }


def _field_from_json(f: dict[str, Any]) -> Field:
    kind: FieldKind = f["kind"]
    ftype: FieldType = f.get("type", "text")
    return Field(
        id=int(f["id"]),
        key=str(f["key"]),
        kind=kind,
        required=bool(f.get("required", True)),
        type=ftype,
        values=tuple(f.get("values") or ()),
        instructions=str(f.get("instructions") or ""),
        default=str(f.get("default") or ""),
    )


def _block_from_json(b: dict[str, Any]) -> ScriptBlock:
    if b["type"] == "field_ref":
        return FieldRef(field_id=int(b["field_id"]))
    return TextBlock(content=str(b.get("content") or ""))


def content_from_json(data: dict[str, Any]) -> AgentVersionContent:
    """Raises DanglingFieldRef / DuplicateFieldKey — the one validation.

    Unreferenced INPUT fields are dropped, not stored: an input field
    exists precisely because the script uses it (FR-AGENT-3B). The chip
    editor cannot produce one; generation can propose one (and has —
    a phantom `ticket_amount` grew a stand-in box for a value no call
    ever spoke), so the boundary normalises it away.
    """
    blocks = tuple(_block_from_json(b) for b in data.get("script_blocks", []))
    referenced = {b.field_id for b in blocks if isinstance(b, FieldRef)}
    fields = tuple(
        f
        for f in (_field_from_json(raw) for raw in data.get("fields", []))
        if f.kind == "output" or f.id in referenced
    )
    return AgentVersionContent(fields=fields, script_blocks=blocks)
