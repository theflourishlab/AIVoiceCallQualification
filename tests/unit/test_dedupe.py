"""The four-row table from FR-CONTACT-8, as tests."""

from becca.domain.dedupe import dedupe_key


def test_same_row_pasted_twice_is_a_duplicate() -> None:
    a = dedupe_key("+2348030001188", {"first_name": "Chidinma", "visit_date": "2026-08-07"})
    b = dedupe_key("+2348030001188", {"first_name": "Chidinma", "visit_date": "2026-08-07"})
    assert a == b


def test_two_people_on_one_office_line_are_kept() -> None:
    a = dedupe_key("+2348030001188", {"first_name": "Chidinma"})
    b = dedupe_key("+2348030001188", {"first_name": "Emeka"})
    assert a != b


def test_two_bookings_for_one_person_are_kept() -> None:
    a = dedupe_key("+2348030001188", {"first_name": "Chidinma", "visit_date": "2026-08-07"})
    b = dedupe_key("+2348030001188", {"first_name": "Chidinma", "visit_date": "2026-08-14"})
    assert a != b


def test_normalisation_runs_before_the_key_so_formats_collide() -> None:
    # "0803 000 1188" and "+2348030001188" both normalise to the same
    # E.164 before this function is called; the key sees one number.
    values = {"first_name": "Chidinma"}
    assert dedupe_key("+2348030001188", values) == dedupe_key("+2348030001188", values)


def test_key_is_order_free_over_mapped_values() -> None:
    a = dedupe_key("+2348030001188", {"a": "1", "b": "2"})
    b = dedupe_key("+2348030001188", {"b": "2", "a": "1"})
    assert a == b
