"""The Phase 1 gate: an agent can be created and reviewed."""

import json

from fastapi.testclient import TestClient

from becca.db.session import SessionFactory
from tests.web.conftest import STAFF_EMAIL, csrf_from, fund_wallet, sign_in


def _create_client_account(console: TestClient, owner_email: str) -> None:
    sign_in(console, STAFF_EMAIL)
    token = csrf_from(console.get("/clients/new").text)
    console.post(
        "/clients",
        data={
            "csrf_token": token,
            "name": "Sylvastar",
            "rate_per_min": "0.30",
            "owner_email": owner_email,
        },
    )
    fund_wallet(console)


def test_brief_to_review_to_new_version(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    _create_client_account(console, "sylvester@sylvastar.ng")
    sign_in(client_plane, "sylvester@sylvastar.ng")

    # Empty state first.
    assert "Build your first agent" in client_plane.get("/").text

    # Brief -> generate (FakeGenerator) -> redirect to review.
    token = csrf_from(client_plane.get("/agents/new").text)
    response = client_plane.post(
        "/agents",
        data={"csrf_token": token, "brief": "Visit Qualifier\nConfirm site visits."},
        follow_redirects=False,
    )
    assert response.status_code == 303
    agent_url = response.headers["location"]

    review = client_plane.get(agent_url)
    assert review.status_code == 200
    # All three views on one screen (FR-AGENT-5).
    assert "The call guide" in review.text
    assert "Fields your contact list must have" in review.text
    assert "What gets extracted after the call" in review.text
    assert "v-chip" in review.text  # chips render, no mustache in the interface
    assert "{{" not in review.text.replace("{{ ", "")  # no literal mustache

    # Saving edited content produces v2 (FR-AGENT-8) with a rename that
    # touches only the field key (FR-AGENT-6).
    content = {
        "fields": [
            {"id": 1, "key": "given_name", "kind": "input", "required": True, "type": "text"},
            {
                "id": 3,
                "key": "still_attending",
                "kind": "output",
                "required": True,
                "type": "enum",
                "values": ["yes", "no"],
            },
        ],
        "script_blocks": [
            {"type": "text", "content": "Hello "},
            {"type": "field_ref", "field_id": 1},
        ],
    }
    token = csrf_from(review.text)
    save = client_plane.post(
        f"{agent_url}/versions",
        data={"csrf_token": token, "content_json": json.dumps(content)},
        follow_redirects=False,
    )
    assert save.status_code == 303
    review2 = client_plane.get(agent_url)
    assert "given_name" in review2.text
    assert "v2" in review2.text


def test_dangling_ref_in_submitted_json_is_rejected(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    """The editor cannot express this; the server boundary still refuses it."""
    _create_client_account(console, "sylvester@sylvastar.ng")
    sign_in(client_plane, "sylvester@sylvastar.ng")
    token = csrf_from(client_plane.get("/agents/new").text)
    agent_url = client_plane.post(
        "/agents",
        data={"csrf_token": token, "brief": "Visit Qualifier"},
        follow_redirects=False,
    ).headers["location"]
    review = client_plane.get(agent_url)

    bad = {
        "fields": [{"id": 1, "key": "a", "kind": "input", "required": True, "type": "text"}],
        "script_blocks": [{"type": "field_ref", "field_id": 99}],
    }
    token = csrf_from(review.text)
    client_plane.post(
        f"{agent_url}/versions",
        data={"csrf_token": token, "content_json": json.dumps(bad)},
        follow_redirects=False,
    )
    # Still v1 — the incoherent structure never became a version.
    assert "v1" in client_plane.get(agent_url).text


async def test_describe_reopens_and_rebuilds_before_launch(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    """14 Aug 2026: the brief is stored, step 1 reopens pre-launch, and
    rebuilding replaces content as a new version while the user's chosen
    name survives."""
    from sqlalchemy import text

    _create_client_account(console, "sylvester@sylvastar.ng")
    sign_in(client_plane, "sylvester@sylvastar.ng")
    token = csrf_from(client_plane.get("/agents/new").text)
    agent_url = client_plane.post(
        "/agents",
        data={"csrf_token": token, "brief": "Visit Qualifier\nCall visitors about slots."},
        follow_redirects=False,
    ).headers["location"]
    agent_id = agent_url.rsplit("/", 1)[1]

    # Rename first — the custom name must survive a rebuild.
    token = csrf_from(client_plane.get(agent_url).text)
    client_plane.post(f"{agent_url}/name", data={"csrf_token": token, "name": "My Qualifier"})

    page = client_plane.get(f"{agent_url}/describe").text
    assert "Call visitors about slots." in page  # the stored brief, editable
    assert "Rebuild from description" in page

    token = csrf_from(page)
    response = client_plane.post(
        f"{agent_url}/describe",
        data={"csrf_token": token, "brief": "Completely new call about invoices."},
        follow_redirects=False,
    )
    assert response.status_code == 303
    review = client_plane.get(agent_url).text
    assert "v2" in review  # rebuilt as a new version
    assert 'value="My Qualifier"' in review  # name untouched
    assert "Completely new call about invoices." in client_plane.get(f"{agent_url}/describe").text

    # Launched agents cannot rebuild.
    async with db.console_session() as s:
        await s.execute(
            text("UPDATE agent SET status = 'finished' WHERE id = :aid"), {"aid": agent_id}
        )
    frozen = client_plane.get(f"{agent_url}/describe").text
    assert "duplicate the agent" in frozen
    assert "Rebuild from description" not in frozen


async def test_delete_agent_removes_everything_it_owns(
    db: SessionFactory, console: TestClient, client_plane: TestClient, app
) -> None:
    """A never-launched agent hard-deletes with its versions, test runs
    and contact lists; its Telnyx scratch assistant goes too."""
    from sqlalchemy import text

    from tests.web.test_billing_groups import _gateway
    from tests.web.test_contacts_flow import CSV

    _create_client_account(console, "sylvester@sylvastar.ng")
    sign_in(client_plane, "sylvester@sylvastar.ng")
    token = csrf_from(client_plane.get("/agents/new").text)
    agent_url = client_plane.post(
        "/agents",
        data={"csrf_token": token, "brief": "Visit Qualifier"},
        follow_redirects=False,
    ).headers["location"]
    agent_id = agent_url.rsplit("/", 1)[1]

    # Give it everything deletable: a test call and a contact list.
    token = csrf_from(client_plane.get(f"{agent_url}/test").text)
    client_plane.post(
        f"{agent_url}/test-calls",
        data={
            "csrf_token": token,
            "to_number": "+2348030001188",
            "standin_first_name": "Chidinma",
            "standin_visit_date": "Thursday 7 August",
        },
    )
    token = csrf_from(client_plane.get(f"/contacts?agent={agent_id}").text)
    client_plane.post(
        "/contacts/upload",
        data={"csrf_token": token, "agent_id": agent_id},
        files={"file": ("visits.csv", CSV.encode(), "text/csv")},
    )
    review = client_plane.get(agent_url).text
    assert "DELETE THIS AGENT" in review

    token = csrf_from(review)
    response = client_plane.post(
        f"{agent_url}/delete", data={"csrf_token": token}, follow_redirects=False
    )
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert client_plane.get(agent_url, follow_redirects=False).status_code == 303  # gone

    async with db.console_session() as s:
        for table in ("agent_version", "test_run", "contact_list"):
            count = (
                await s.execute(
                    text(f"SELECT count(*) FROM {table} WHERE agent_id = :aid"),
                    {"aid": agent_id},
                )
            ).scalar_one()
            assert count == 0, table
    gateway = _gateway(app)
    assert not any(a["name"].startswith("scratch") for a in gateway.assistants.values())

    # Billing reconciliation survives the delete (§11a): the audit row
    # carries the test calls' Telnyx session ids, so their detail-record
    # dollars stay explainable after the rows are gone.
    async with db.console_session() as s:
        meta = (
            await s.execute(
                text(
                    "SELECT metadata FROM audit_log WHERE action = 'deleted_agent'"
                    " AND target = :aid ORDER BY created_at DESC LIMIT 1"
                ),
                {"aid": agent_id},
            )
        ).scalar_one()
    import json as _json

    meta = meta if isinstance(meta, dict) else _json.loads(meta)
    assert meta["test_calls"] and meta["test_calls"][0]["call_sid"].startswith("fake-callsid")


async def test_delete_refused_for_launched_agents(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    from sqlalchemy import text

    _create_client_account(console, "sylvester@sylvastar.ng")
    sign_in(client_plane, "sylvester@sylvastar.ng")
    token = csrf_from(client_plane.get("/agents/new").text)
    agent_url = client_plane.post(
        "/agents",
        data={"csrf_token": token, "brief": "Visit Qualifier"},
        follow_redirects=False,
    ).headers["location"]
    agent_id = agent_url.rsplit("/", 1)[1]

    async with db.console_session() as s:
        await s.execute(
            text("UPDATE agent SET status = 'finished' WHERE id = :aid"), {"aid": agent_id}
        )
    review = client_plane.get(agent_url).text
    assert "DELETE THIS AGENT" not in review
    assert "CANNOT BE DELETED" in review
    token = csrf_from(review)
    client_plane.post(f"{agent_url}/delete", data={"csrf_token": token})
    assert client_plane.get(agent_url).status_code == 200  # still here
