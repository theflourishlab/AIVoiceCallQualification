"""Phase 2 gate: the schema can be iterated against (fake) calls —
scratch lifecycle, snapshot, results, diff, regenerate, draft→tested."""

from fastapi.testclient import TestClient
from sqlalchemy import text

from becca.db.session import SessionFactory
from tests.web.conftest import csrf_from, sign_in
from tests.web.test_agent_flow import _create_client_account


def _build_agent(client_plane: TestClient) -> str:
    token = csrf_from(client_plane.get("/agents/new").text)
    return client_plane.post(
        "/agents",
        data={"csrf_token": token, "brief": "Visit Qualifier"},
        follow_redirects=False,
    ).headers["location"]


def test_full_test_loop(db: SessionFactory, console: TestClient, client_plane: TestClient) -> None:
    _create_client_account(console, "sylvester@sylvastar.ng")
    sign_in(client_plane, "sylvester@sylvastar.ng")
    agent_url = _build_agent(client_plane)

    # The test screen renders with the call form and empty history.
    page = client_plane.get(f"{agent_url}/test")
    assert page.status_code == 200
    assert "Run test 1" in page.text
    assert "No tests yet" in page.text

    # Place a test call (fake gateway: scratch assistant is created, the
    # call exists immediately).
    token = csrf_from(page.text)
    response = client_plane.post(
        f"{agent_url}/test-calls",
        data={
            "csrf_token": token,
            "to_number": "+2348030001188",
            "standin_first_name": "Chidinma",
            "standin_visit_date": "Thursday 7 August",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    # Poll-on-view completes the run: results, transcript, NEW markers.
    page = client_plane.get(f"{agent_url}/test")
    assert "Test 1 — the answers" in page.text
    assert "Answers back" in page.text
    assert "still_attending" in page.text
    assert "tag var" in page.text  # new values highlighted
    assert "TRANSCRIPT" in page.text

    # FR-TEST-6: the agent is now tested.
    assert ">tested<" in client_plane.get("/").text or "tested" in client_plane.get("/").text

    # Second call: diff against the first (fake results are stable for
    # enums, so they read unchanged; the summary text varies).
    token = csrf_from(page.text)
    client_plane.post(
        f"{agent_url}/test-calls",
        data={
            "csrf_token": token,
            "to_number": "+2348030001188",
            "standin_first_name": "Chidinma",
            "standin_visit_date": "Thursday 7 August",
        },
    )
    page = client_plane.get(f"{agent_url}/test")
    assert "Test 2 — the answers" in page.text
    assert "tag var" in page.text  # changed values keep the highlight

    # Describe-and-regenerate creates a new version (FR-TEST-9).
    token = csrf_from(page.text)
    client_plane.post(
        f"{agent_url}/regenerate",
        data={"csrf_token": token, "problem": "It read the date as numbers."},
    )
    page = client_plane.get(f"{agent_url}/test")
    assert "v2" in page.text


def test_schema_edit_on_test_screen_creates_version_and_returns(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    _create_client_account(console, "sylvester@sylvastar.ng")
    sign_in(client_plane, "sylvester@sylvastar.ng")
    agent_url = _build_agent(client_plane)
    page = client_plane.get(f"{agent_url}/test")
    token = csrf_from(page.text)

    content = {
        "fields": [
            {"id": 1, "key": "first_name", "kind": "input", "required": True, "type": "text"},
            {
                "id": 3,
                "key": "still_attending",
                "kind": "output",
                "required": True,
                "type": "enum",
                "values": ["yes", "no", "unsure", "callback"],
            },
        ],
        "script_blocks": [
            {"type": "text", "content": "Hello "},
            {"type": "field_ref", "field_id": 1},
        ],
    }
    import json

    response = client_plane.post(
        f"{agent_url}/versions",
        data={
            "csrf_token": token,
            "content_json": json.dumps(content),
            "return_to": "test",
        },
        follow_redirects=False,
    )
    assert response.headers["location"].endswith("/test")
    page = client_plane.get(f"{agent_url}/test")
    assert "v2" in page.text
    assert "callback" in page.text


async def test_stranded_dialling_run_times_out_without_scratch_assistant(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    """A run left dialling after its agent's scratch assistant is gone
    (the sweeper clears the ids once a launch orphans it) must fail out
    on the next view — otherwise the test screen meta-refreshes forever
    and wipes form edits (observed live 13 Aug 2026, test run 11)."""
    _create_client_account(console, "sylvester@sylvastar.ng")
    sign_in(client_plane, "sylvester@sylvastar.ng")
    agent_url = _build_agent(client_plane)
    page = client_plane.get(f"{agent_url}/test")
    token = csrf_from(page.text)
    client_plane.post(
        f"{agent_url}/test-calls",
        data={
            "csrf_token": token,
            "to_number": "+2348030001188",
            "standin_first_name": "Chidinma",
            "standin_visit_date": "Thursday 7 August",
        },
    )
    agent_id = agent_url.rsplit("/", 1)[1]
    # Strand the run the way the live bug did: dialling, never matched to
    # a conversation, scratch assistant gone, older than the 90s tier.
    async with db.console_session() as s:
        await s.execute(
            text(
                "UPDATE test_run SET status = 'dialling', telnyx_conversation_id = NULL,"
                " created_at = now() - interval '10 minutes' WHERE agent_id = :aid"
            ),
            {"aid": agent_id},
        )
        await s.execute(
            text("UPDATE agent SET telnyx_scratch_assistant_id = NULL WHERE id = :aid"),
            {"aid": agent_id},
        )

    page = client_plane.get(f"{agent_url}/test")
    assert page.status_code == 200
    assert 'http-equiv="refresh"' not in page.text  # the refresh loop is broken
    async with db.console_session() as s:
        status = (
            await s.execute(
                text("SELECT status FROM test_run WHERE agent_id = :aid"),
                {"aid": agent_id},
            )
        ).scalar_one()
    assert status == "failed"


async def test_finished_agent_screen_is_frozen_but_still_callable(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    """FR-LAUNCH-7 on the test screen: a finished agent shows the freeze
    (no save, no regenerate) instead of advertising edits the service
    will refuse — but test calls remain available, and a direct POST to
    /regenerate redirects without burning a generation call."""
    _create_client_account(console, "sylvester@sylvastar.ng")
    sign_in(client_plane, "sylvester@sylvastar.ng")
    agent_url = _build_agent(client_plane)
    agent_id = agent_url.rsplit("/", 1)[1]
    async with db.console_session() as s:
        await s.execute(
            text("UPDATE agent SET status = 'finished' WHERE id = :aid"),
            {"aid": agent_id},
        )

    page = client_plane.get(f"{agent_url}/test")
    assert page.status_code == 200
    assert "SCHEMA FROZEN" in page.text
    assert "Save as v" not in page.text
    assert "Fix the guide" not in page.text
    assert "Run test" in page.text  # testing a finished agent stays allowed
    assert "DUPLICATE THE AGENT" in page.text

    # The endpoint itself refuses politely (no 500, no new version).
    token = csrf_from(page.text)
    response = client_plane.post(
        f"{agent_url}/regenerate",
        data={"csrf_token": token, "problem": "sounded stiff"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("/test")
    async with db.console_session() as s:
        versions = (
            await s.execute(
                text("SELECT count(*) FROM agent_version WHERE agent_id = :aid"),
                {"aid": agent_id},
            )
        ).scalar_one()
    assert versions == 1


async def test_voice_screen_choices_reach_the_scratch_assistant(
    db: SessionFactory, console: TestClient, client_plane: TestClient, app
) -> None:
    """Strand 3 end-to-end: choices saved on the voice screen land on
    the assistant, and reach an EXISTING scratch assistant on the next
    test call (the sync re-asserts every behaviour field). No assertion
    against ambient env defaults — only against the saved overrides."""
    from becca.services.voice_config import VOICE_CATALOG
    from tests.web.test_billing_groups import _gateway

    _create_client_account(console, "sylvester@sylvastar.ng")
    sign_in(client_plane, "sylvester@sylvastar.ng")
    agent_url = _build_agent(client_plane)
    gateway = _gateway(app)

    def _place() -> None:
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

    _place()  # scratch assistant now exists, built from defaults
    scratch = next(a for a in gateway.assistants.values() if a["name"].startswith("scratch"))

    page = client_plane.get(f"{agent_url}/voice")
    assert page.status_code == 200
    assert "How it should sound" in page.text
    barry = next(v.id for v in VOICE_CATALOG if v.name == "Barry")
    token = csrf_from(page.text)
    response = client_plane.post(
        f"{agent_url}/voice",
        data={
            "csrf_token": token,
            "voice": barry,
            "model": "openai/gpt-4.1",
            "voice_speed": "1.25",
            "transcription_model": "deepgram/nova-3",
            # interruption checkbox omitted = off
            "time_limit_secs": "240",
            "idle_timeout_secs": "9000",  # clamps to 600
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    _place()  # the sync pushes the new behaviour to the existing assistant
    assert scratch["model"] == "openai/gpt-4.1"
    assert scratch["voice"] == barry
    assert scratch["voice_speed"] == 1.25
    assert scratch["transcription_model"] == "deepgram/nova-3"
    assert scratch["interruption"] is False
    assert scratch["time_limit_secs"] == 240
    assert scratch["idle_timeout_secs"] == 600


async def test_voice_preview_is_proxied_and_catalog_only(
    db: SessionFactory, console: TestClient, client_plane: TestClient, app
) -> None:
    """FR-AGENT-11: preview returns audio through our backend, clamps
    speed to 0.5-2.0, and refuses non-catalog voice ids."""
    from becca.services.voice_config import VOICE_CATALOG
    from tests.web.test_billing_groups import _gateway

    _create_client_account(console, "sylvester@sylvastar.ng")
    sign_in(client_plane, "sylvester@sylvastar.ng")
    agent_url = _build_agent(client_plane)
    gateway = _gateway(app)

    voice = VOICE_CATALOG[0].id
    response = client_plane.get(f"{agent_url}/voice/preview?voice={voice}&speed=9")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert gateway.previews[-1]["voice_speed"] == 2.0  # clamped

    assert (
        client_plane.get(f"{agent_url}/voice/preview?voice=Telnyx.Ultra.rogue").status_code == 404
    )


def test_review_flow_links_and_rename(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    """14 Aug 2026 flow feedback: review continues to VOICE (step 3, not
    straight to test); the manual-edit save says what it does; the test
    screen offers a direct path to contacts; and the agent's name is an
    editable heading that renames everywhere."""
    _create_client_account(console, "sylvester@sylvastar.ng")
    sign_in(client_plane, "sylvester@sylvastar.ng")
    agent_url = _build_agent(client_plane)

    review = client_plane.get(agent_url).text
    assert "Continue to voice" in review
    assert f'href="{agent_url}/voice"' in review
    assert "Save edits as v" in review
    assert "Save as draft" not in review
    assert f'action="{agent_url}/name"' in review  # the editable heading

    test_page = client_plane.get(f"{agent_url}/test").text
    agent_id = agent_url.rsplit("/", 1)[1]
    assert f'href="/contacts?agent={agent_id}"' in test_page
    assert "Continue to contacts" in test_page
    assert "Promote and lock schema" not in test_page  # stale button retired

    token = csrf_from(review)
    response = client_plane.post(
        f"{agent_url}/name",
        data={"csrf_token": token, "name": "  Lagos   Visit Qualifier  "},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Lagos Visit Qualifier" in client_plane.get("/").text  # normalised, everywhere
