"""The one artifact: a field set and a script (FR-AGENT-2).

An agent version stores one list of fields and one script. The script is
a sequence of blocks; a block is literal text or a pointer to a field BY
ID (FR-AGENT-3). A field's name exists in exactly one place — on the
field — so renaming writes one value and nothing can fall out of sync.

This package is pure: no SQLAlchemy, no httpx, no FastAPI. Ever.
"""

from dataclasses import dataclass, field, replace
from typing import Literal, Self

FieldKind = Literal["input", "output"]
FieldType = Literal["text", "enum"]


@dataclass(frozen=True)
class Field:
    """One named thing the agent either needs or captures."""

    id: int
    key: str
    kind: FieldKind
    required: bool = True
    type: FieldType = "text"
    values: tuple[str, ...] = ()
    instructions: str = ""
    # For an OPTIONAL input: what a contact row missing the value
    # resolves to (FR-CONTACT-9). Must read naturally when spoken —
    # "a consultant", never an empty string.
    default: str = ""


@dataclass(frozen=True)
class TextBlock:
    content: str
    type: Literal["text"] = "text"


@dataclass(frozen=True)
class FieldRef:
    """A pointer to a field by id — never by name (FR-AGENT-3)."""

    field_id: int
    type: Literal["field_ref"] = "field_ref"


ScriptBlock = TextBlock | FieldRef


class DanglingFieldRef(Exception):
    """A field_ref points at a field id absent from the field set.

    Only representable at the one boundary where structure arrives from
    outside — model output becoming a domain object (FR-AGENT-3A). The
    caller retries generation; this is never surfaced to a user.
    """

    def __init__(self, field_ids: frozenset[int]) -> None:
        self.field_ids = field_ids
        super().__init__(f"field_refs point at missing field ids: {sorted(field_ids)}")


class DuplicateFieldKey(Exception):
    """Two fields share a key. One list, unique keys (FR-AGENT-3)."""


@dataclass(frozen=True)
class AgentVersionContent:
    """The whole of what an agent version stores about its content.

    Construction validates coherence (the FR-AGENT-3A boundary). Once an
    instance exists, a dangling reference or a duplicate key has no
    representation, so downstream code never checks.
    """

    fields: tuple[Field, ...]
    script_blocks: tuple[ScriptBlock, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise DuplicateFieldKey(
                f"duplicate keys: {sorted(k for k in keys if keys.count(k) > 1)}"
            )
        ids = {f.id for f in self.fields}
        dangling = frozenset(
            b.field_id for b in self.script_blocks if isinstance(b, FieldRef)
        ) - frozenset(ids)
        if dangling:
            raise DanglingFieldRef(dangling)

    def field_by_id(self, field_id: int) -> Field:
        for f in self.fields:
            if f.id == field_id:
                return f
        raise KeyError(field_id)

    def referenced_field_ids(self) -> frozenset[int]:
        return frozenset(b.field_id for b in self.script_blocks if isinstance(b, FieldRef))

    def rename_field(self, field_id: int, new_key: str) -> Self:
        """A rename writes one value (FR-AGENT-6).

        The script and any column mapping point at the id and are
        untouched; there is no propagation step and no partial rename.
        """
        return replace(
            self,
            fields=tuple(replace(f, key=new_key) if f.id == field_id else f for f in self.fields),
        )
