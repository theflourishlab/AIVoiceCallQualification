"""The Phase 3 gate: a list validates against the variable contract."""

from fastapi.testclient import TestClient

from becca.db.session import SessionFactory
from tests.web.conftest import STAFF_EMAIL, csrf_from, fund_wallet, sign_in

OWNER = "sylvester@sylvastar.ng"

# FakeGenerator's contract: first_name (id 1) and visit_date (id 2), both
# required. "Slot" will not auto-map to visit_date — that is the point.
CSV = (
    "First Name,Phone,Slot,Source\n"
    "Chidinma,0803 000 1188,11:30,walk-in\n"
    "Ngozi,not-a-number,09:15,ad\n"
    "Chidinma,0803 000 1188,11:30,walk-in\n"
    ",0803 000 1190,10:00,ad\n"
    "Adaeze,0803 000 1189,14:00,referral\n"
)


def _setup_agent(console: TestClient, client_plane: TestClient) -> str:
    sign_in(console, STAFF_EMAIL)
    token = csrf_from(console.get("/clients/new").text)
    console.post(
        "/clients",
        data={
            "csrf_token": token,
            "name": "Sylvastar",
            "rate_per_min": "0.30",
            "owner_email": OWNER,
        },
    )
    fund_wallet(console)
    sign_in(client_plane, OWNER)
    token = csrf_from(client_plane.get("/agents/new").text)
    location: str = client_plane.post(
        "/agents",
        data={"csrf_token": token, "brief": "Visit Qualifier"},
        follow_redirects=False,
    ).headers["location"]
    return location.rsplit("/", 1)[1]


def _upload(client_plane: TestClient, agent_id: str, body: str = CSV) -> str:
    token = csrf_from(client_plane.get(f"/contacts?agent={agent_id}").text)
    response = client_plane.post(
        "/contacts/upload",
        data={"csrf_token": token, "agent_id": agent_id},
        files={"file": ("site-visits-july.csv", body.encode(), "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    location: str = response.headers["location"]
    assert location.startswith("/contacts/")
    return location


def test_agent_first_gate(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    """FR-CONTACT-1: no upload until an agent is picked, because the
    agent decides the required columns — which the cards show."""
    agent_id = _setup_agent(console, client_plane)
    page = client_plane.get("/contacts").text
    assert "PICK AN AGENT TO ENABLE" in page
    # Redesign (14 Aug 2026): column requirements wait until an agent is
    # picked — they appear once, where they are actionable.
    assert "first_name" not in page and "visit_date" not in page
    assert "RUN A TEST CALL FIRST" in page  # the row nudges towards a test call

    picked = client_plane.get(f"/contacts?agent={agent_id}").text
    assert "Upload and map" in picked  # selection enables the upload
    assert "first_name" in picked and "visit_date" in picked  # the demanded columns


def test_upload_map_recheck(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    agent_id = _setup_agent(console, client_plane)
    list_url = _upload(client_plane, agent_id)

    page = client_plane.get(list_url).text
    # visit_date found no column: the one blocking error (FR-CONTACT-5).
    assert "1 required unmapped" in page
    assert "visit_date" in page
    # Exclusions marked, not dropped (FR-CONTACT-3/8/9): one unparseable,
    # one missing first_name, one exact duplicate collapsed.
    assert "3 rows will not be dialled" in page
    assert "not-a-number" in page  # retained and shown for review
    assert "1 identical row kept once" in page
    # diallable / file rows, as the verdict strip renders them
    assert '<span class="num">2</span> of <span class="num">5</span> rows diallable' in page

    # Map Slot -> visit_date (by field id, FR-CONTACT-4) and re-check.
    token = csrf_from(page)
    save = client_plane.post(
        f"{list_url}/mapping",
        data={
            "csrf_token": token,
            "col_0": "1",
            "col_1": "phone",
            "col_2": "2",
            "col_3": "",
        },
        follow_redirects=False,
    )
    assert save.status_code == 303
    rechecked = client_plane.get(list_url).text
    assert "All required mapped" in rechecked


def test_make_optional_with_spoken_default(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    """FR-CONTACT-5's second remedy, honouring FR-CONTACT-9's default."""
    agent_id = _setup_agent(console, client_plane)
    list_url = _upload(client_plane, agent_id)
    page = client_plane.get(list_url).text
    token = csrf_from(page)

    # An empty default is refused — it must read naturally when spoken.
    refused = client_plane.post(
        f"{list_url}/make-optional",
        data={"csrf_token": token, "field_id": "2", "default": "   "},
        follow_redirects=False,
    )
    assert refused.headers["location"].endswith("error=default")

    accepted = client_plane.post(
        f"{list_url}/make-optional",
        data={"csrf_token": token, "field_id": "2", "default": "your booked time"},
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    page = client_plane.get(list_url).text
    assert "All required mapped" in page
    assert "v2" in page  # making it optional versioned the agent (FR-AGENT-8)


def test_unreadable_upload_is_refused_kindly(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    agent_id = _setup_agent(console, client_plane)
    token = csrf_from(client_plane.get(f"/contacts?agent={agent_id}").text)
    response = client_plane.post(
        "/contacts/upload",
        data={"csrf_token": token, "agent_id": agent_id},
        files={"file": ("empty.csv", b"", "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error=unreadable" in response.headers["location"]
    assert "could not be read" in client_plane.get(response.headers["location"]).text


def test_lists_are_tenant_scoped(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    """A second client cannot open the first client's list URL."""
    agent_id = _setup_agent(console, client_plane)
    list_url = _upload(client_plane, agent_id)

    sign_in(console, STAFF_EMAIL)
    token = csrf_from(console.get("/clients/new").text)
    console.post(
        "/clients",
        data={
            "csrf_token": token,
            "name": "Lekki Gardens",
            "rate_per_min": "0.30",
            "owner_email": "owner@lekki.ng",
        },
    )
    sign_in(client_plane, "owner@lekki.ng")
    response = client_plane.get(list_url, follow_redirects=False)
    assert response.status_code == 303  # RLS found nothing; back to /contacts
    assert response.headers["location"] == "/contacts"


def test_redesigned_pick_states(
    db: SessionFactory, console: TestClient, client_plane: TestClient
) -> None:
    """The 14 Aug 2026 redesign's three stateful claims: the unfinished-
    import strip exists only while an orphan does; an unmapped list never
    counts as contacts; a mapped list flips the row to HAS CONTACTS and
    the upload panel to add-another framing."""
    agent_id = _setup_agent(console, client_plane)
    assert "Unfinished import" not in client_plane.get("/contacts").text

    # A (fake) test call makes the agent's row pickable.
    token = csrf_from(client_plane.get(f"/agents/{agent_id}/test").text)
    client_plane.post(
        f"/agents/{agent_id}/test-calls",
        data={
            "csrf_token": token,
            "to_number": "+2348030001188",
            "standin_first_name": "Chidinma",
            "standin_visit_date": "Thursday 7 August",
        },
    )

    # Upload leaves visit_date unmapped -> the list is an orphan.
    list_url = _upload(client_plane, agent_id)
    page = client_plane.get("/contacts").text
    assert "Unfinished import" in page and "Resume mapping" in page
    assert "NO CONTACTS YET" in page  # an unfinished list counts nothing

    # Finish the mapping (Slot -> visit_date, by field id).
    token = csrf_from(client_plane.get(list_url).text)
    client_plane.post(
        f"{list_url}/mapping",
        data={"csrf_token": token, "col_0": "1", "col_1": "phone", "col_2": "2", "col_3": ""},
        follow_redirects=False,
    )

    page = client_plane.get("/contacts").text
    assert "Unfinished import" not in page  # the strip retired itself
    assert "HAS CONTACTS" in page and ">2</b>" in page  # the real diallable count

    picked = client_plane.get(f"/contacts?agent={agent_id}").text
    assert "This agent already has contacts" in picked
    assert "UPLOAD ANOTHER LIST" in picked  # add-semantics, nothing overwritten
