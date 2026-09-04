"""/healthz answers GET and HEAD on every hostname, before auth, so an
uptime pinger (UptimeRobot probes with HEAD) keeps the free-tier demo
awake instead of reading a 405."""

from fastapi.testclient import TestClient


def test_healthz_get_and_head_on_both_planes(console: TestClient, client_plane: TestClient) -> None:
    for plane in (console, client_plane):
        get = plane.get("/healthz", follow_redirects=False)
        assert get.status_code == 200
        assert get.text == "ok"
        head = plane.head("/healthz", follow_redirects=False)
        assert head.status_code == 200
        assert head.text == ""
