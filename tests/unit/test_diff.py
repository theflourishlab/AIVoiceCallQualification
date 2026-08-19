from becca.domain.model import AgentVersionContent, Field
from becca.services.testing import diff_results

CONTENT = AgentVersionContent(
    fields=(
        Field(id=3, key="still_attending", kind="output", type="enum", values=("yes", "no")),
        Field(id=4, key="budget_band", kind="output", type="enum", values=("under_50m",)),
    )
)


def test_first_run_is_all_new() -> None:
    diffs = diff_results(CONTENT, {"3": "yes", "4": "under_50m"}, None)
    assert all(d.change == "new" for d in diffs)


def test_changed_value_is_flagged_with_previous() -> None:
    diffs = diff_results(CONTENT, {"3": "yes", "4": "50_80m"}, {"3": "yes", "4": "under_50m"})
    by_key = {d.key: d for d in diffs}
    assert by_key["still_attending"].change == "unchanged"
    assert by_key["budget_band"].change == "changed"
    assert by_key["budget_band"].previous == "under_50m"


def test_diff_keys_follow_renames_because_ids_anchor() -> None:
    renamed = CONTENT.rename_field(3, "attending")
    diffs = diff_results(renamed, {"3": "no"}, {"3": "yes"})
    assert diffs[0].key == "attending"
    assert diffs[0].change == "changed"
