"""The import pipeline, no DB: parse, suggest, normalise, dedupe,
completeness (FRD §6)."""

import io

import pytest

from becca.domain.model import Field
from becca.services.contacts import (
    Mapping,
    ParsedFile,
    UnreadableFile,
    compute_import,
    normalise_phone,
    parse_upload,
    phone_column,
    resolved_variables,
    suggest_mapping,
    unmapped_required,
)

FIRST_NAME = Field(id=1, key="first_name", kind="input")
VISIT_DATE = Field(id=2, key="visit_date", kind="input")
CONSULTANT = Field(
    id=5, key="consultant_name", kind="input", required=False, default="a consultant"
)
CONTRACT = (FIRST_NAME, VISIT_DATE, CONSULTANT)


def _mapping(**over: int | str | None) -> Mapping:
    base: Mapping = {"Phone": "phone", "First Name": 1, "Visit Date": 2}
    base.update(over)
    return base


def _parsed(*rows: tuple[str, ...]) -> ParsedFile:
    return ParsedFile(headers=("Phone", "First Name", "Visit Date"), rows=tuple(rows))


# ---------------------------------------------------------------- parsing


def test_csv_parse_detects_headers_and_skips_blank_rows() -> None:
    bom = "﻿"
    data = f"{bom}Full Name,Phone\n\nChidinma,0803 000 1188\n,\nNgozi,0803 000 1189\n".encode()
    parsed = parse_upload("contacts.csv", data)
    assert parsed.headers == ("Full Name", "Phone")
    assert parsed.rows == (("Chidinma", "0803 000 1188"), ("Ngozi", "0803 000 1189"))


def test_csv_semicolon_dialect_is_sniffed() -> None:
    parsed = parse_upload("l.csv", b"Name;Phone\nAda;08030001188\n")
    assert parsed.headers == ("Name", "Phone")


def test_duplicate_and_blank_headers_get_distinct_names() -> None:
    parsed = parse_upload("l.csv", b"Phone,,Phone\n1,2,3\n")
    assert parsed.headers == ("Phone", "Column 2", "Phone (2)")


def test_empty_file_is_unreadable() -> None:
    with pytest.raises(UnreadableFile):
        parse_upload("l.csv", b"")
    with pytest.raises(UnreadableFile):
        parse_upload("l.csv", b"Name,Phone\n")  # headers, nobody to call


def test_xlsx_parses_with_number_and_date_cells() -> None:
    from datetime import datetime

    from openpyxl.workbook import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["Full Name", "Phone", "Visit Date"])
    # Excel types phone columns as numbers and dates as datetimes.
    sheet.append(["Chidinma", 2348030001188, datetime(2026, 8, 7)])
    buffer = io.BytesIO()
    book.save(buffer)

    parsed = parse_upload("visits.xlsx", buffer.getvalue())
    assert parsed.headers == ("Full Name", "Phone", "Visit Date")
    assert parsed.rows == (("Chidinma", "2348030001188", "2026-08-07"),)


# ------------------------------------------------------------- suggestion


def test_mapping_suggested_by_name_similarity() -> None:
    headers = ("Full Name", "Phone", "Visit Date", "Source")
    mapping = suggest_mapping(headers, CONTRACT)
    assert mapping["Phone"] == "phone"
    assert mapping["Full Name"] == FIRST_NAME.id
    assert mapping["Visit Date"] == VISIT_DATE.id
    assert mapping["Source"] is None  # kept as metadata, never guessed


def test_each_field_wins_at_most_one_column() -> None:
    mapping = suggest_mapping(("first_name", "First Name"), (FIRST_NAME,))
    assert [v for v in mapping.values() if v == FIRST_NAME.id] == [FIRST_NAME.id]


def test_unmapped_required_lists_blockers() -> None:
    mapping = _mapping(**{"Visit Date": None})
    assert unmapped_required(CONTRACT, mapping) == (VISIT_DATE,)
    assert phone_column(mapping) == "Phone"


# ---------------------------------------------------------- normalisation


def test_nigerian_numbers_normalise_to_e164() -> None:
    assert normalise_phone("0803 000 1188") == "+2348030001188"
    assert normalise_phone("+234 803 000 1188") == "+2348030001188"
    assert normalise_phone("banana") is None
    assert normalise_phone("") is None
    assert normalise_phone("0803") is None  # too short to be a number


# -------------------------------------------------- dedupe (FR-CONTACT-8)


def test_the_four_row_dedupe_table() -> None:
    computed = compute_import(
        _parsed(
            ("0803 000 1188", "Chidinma", "7 Aug"),
            ("0803 000 1188", "Chidinma", "7 Aug"),  # same row pasted twice
            ("0803 000 1188", "Adaeze", "7 Aug"),  # shared office line
            ("0803 000 1188", "Chidinma", "19 Aug"),  # second appointment
            ("+2348030001188", "Chidinma", "7 Aug"),  # normalisation collides
        ),
        CONTRACT,
        _mapping(),
    )
    kept = [(r.variables.get("1"), r.variables.get("2")) for r in computed.rows]
    assert kept == [("Chidinma", "7 Aug"), ("Adaeze", "7 Aug"), ("Chidinma", "19 Aug")]
    assert computed.duplicate_count == 2
    assert all(r.diallable for r in computed.rows)


def test_identical_unparseable_rows_still_collapse() -> None:
    computed = compute_import(
        _parsed(("banana", "Ada", "7 Aug"), ("banana", "Ada", "7 Aug")),
        CONTRACT,
        _mapping(),
    )
    assert len(computed.rows) == 1
    assert computed.duplicate_count == 1


# --------------------------------------------- completeness (FR-CONTACT-9)


def test_row_exclusions_are_marked_not_dropped() -> None:
    computed = compute_import(
        _parsed(
            ("0803 000 1188", "Chidinma", "7 Aug"),
            ("not-a-number", "Ngozi", "8 Aug"),
            ("0803 000 1190", "", "9 Aug"),  # required value missing
        ),
        CONTRACT,
        _mapping(),
    )
    by_reason = {r.exclusion_reason: r for r in computed.rows}
    assert computed.diallable_count == 1
    assert by_reason["unparseable_number"].phone_raw == "not-a-number"
    assert by_reason["missing_required_value"].phone_e164 == "+2348030001190"
    assert len(computed.rows) == 3  # all retained (FR-CONTACT-3/9)


def test_unmapped_required_field_is_not_a_row_defect() -> None:
    """An entirely unmapped required field blocks the LIST (FR-CONTACT-5);
    rows are judged only over mapped required fields."""
    computed = compute_import(
        _parsed(("0803 000 1188", "Chidinma", "7 Aug")),
        CONTRACT,
        _mapping(**{"Visit Date": None}),
    )
    assert computed.rows[0].diallable


def test_optional_missing_value_resolves_to_spoken_default() -> None:
    row = compute_import(
        _parsed(("0803 000 1188", "Chidinma", "7 Aug")),
        CONTRACT,
        _mapping(),
    ).rows[0]
    assert row.diallable  # optional absence never excludes
    resolved = resolved_variables(CONTRACT, row.variables)
    assert resolved[CONSULTANT.id] == "a consultant"
    assert resolved[FIRST_NAME.id] == "Chidinma"
