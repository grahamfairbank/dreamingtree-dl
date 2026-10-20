from fastapi.testclient import TestClient

from app.backend import FakeBackend
from app.server import create_app


def client() -> TestClient:
    return TestClient(create_app(FakeBackend()))


def test_health_and_index():
    c = client()
    assert c.get("/healthz").json() == {"ok": True}
    r = c.get("/")
    assert r.status_code == 200 and "dreamingtree-dl" in r.text


def test_status():
    s = client().get("/api/status").json()
    assert s["dreamingtree"]["ok"] is True
    assert s["qbittorrent"]["ok"] is True
    assert s["staging_free_bytes"] > 0


def test_listing_paging_and_filters():
    c = client()
    first = c.get("/api/listing").json()
    assert first["page"] == 1 and first["pages"] >= 2 and len(first["rows"]) == 25
    assert "Dave Matthews Band" in first["artists"]
    sbd = c.get("/api/listing", params={"source": "sbd"}).json()
    assert sbd["rows"] and all(r["source"] == "sbd" for r in sbd["rows"])
    art = c.get("/api/listing", params={"artist": "Dave Matthews & Tim Reynolds"}).json()
    assert all(r["artist"] == "Dave Matthews & Tim Reynolds" for r in art["rows"])
    txt = c.get("/api/listing", params={"q": "dmb2025-07-12"}).json()
    assert [r["name"] for r in txt["rows"]] == ["dmb2025-07-12.ca-11.flac16"]
    yr = c.get("/api/listing", params={"year": "2025"}).json()
    assert yr["rows"] and all(r["show_date"].startswith("2025") for r in yr["rows"])


def test_queue_and_jobs():
    c = client()
    rows = c.get("/api/listing", params={"q": "d&t2007"}).json()["rows"]
    assert rows and rows[0]["mark"] is None
    r = c.post("/api/queue", json={"ids": [rows[0]["torrent_id"]], "destination": "Dave Matthews Band"})
    assert r.status_code == 200 and len(r.json()["jobs"]) == 1
    marked = c.get("/api/listing", params={"q": "d&t2007"}).json()["rows"][0]
    assert marked["mark"] == "queued"
    assert any(j["torrent_id"] == rows[0]["torrent_id"] for j in c.get("/api/jobs").json()["jobs"])


def test_queue_errors():
    c = client()
    assert c.post("/api/queue", json={"ids": [], "destination": "Dave Matthews Band"}).status_code == 400
    assert c.post("/api/queue", json={"ids": ["1005"], "destination": "nope"}).status_code == 404


def test_job_events_retry_cancel():
    c = client()
    jobs = {j["state"]: j for j in c.get("/api/jobs").json()["jobs"]}
    err, dl = jobs["error"], jobs["downloading"]
    assert c.get(f"/api/jobs/{err['id']}/events").json()["events"]
    assert c.get("/api/jobs/999/events").status_code == 404
    assert c.post(f"/api/jobs/{dl['id']}/retry").status_code == 400
    assert c.post(f"/api/jobs/{err['id']}/retry").json()["state"] == "queued"
    assert c.post(f"/api/jobs/{dl['id']}/cancel").json()["state"] == "cancelled"
    assert c.post("/api/jobs/999/cancel").status_code == 404


def test_settings_never_contain_password():
    c = client()
    body = c.get("/api/settings").text
    assert "demo-secret-do-not-leak" not in body
    s = c.get("/api/settings").json()
    assert "password" not in s["dreamingtree"] and s["dreamingtree"]["password_set"] is True
    r = c.put("/api/settings", json={"retention_days": 30, "dreamingtree": {"username": "bob", "password": ""}})
    assert r.status_code == 200
    out = r.json()
    assert out["retention_days"] == 30 and out["dreamingtree"]["username"] == "bob"
    assert "demo-secret-do-not-leak" not in r.text and out["dreamingtree"]["password_set"] is True
    r = c.put("/api/settings", json={"dreamingtree": {"password": "new-pass-xyz"}})
    assert "new-pass-xyz" not in r.text


def test_settings_validation_error():
    assert client().put("/api/settings", json={"retention_days": "abc"}).status_code in (400, 422, 500)


def test_relogin():
    assert client().post("/api/relogin").json()["ok"] is True
