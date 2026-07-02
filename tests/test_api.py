"""REST API surface, via FastAPI TestClient (no voice deps needed)."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(fresh_db):
    from outreach.server import app

    with TestClient(app) as c:
        yield c


def test_health_and_agents(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert "seaside-hotel" in r.json()["agents"]
    assert "lead-gen-demo" in r.json()["agents"]

    agents = client.get("/api/v1/agents").json()
    ids = {a["agent_id"] for a in agents}
    assert {"seaside-hotel", "lead-gen-demo"} <= ids
    demo = next(a for a in agents if a["agent_id"] == "lead-gen-demo")
    assert demo["profile"] == "budget"
    assert demo["language"] == "hi"


def test_lead_crud_and_csv_import(client):
    r = client.post("/api/v1/leads", json=[{"phone": "+919876543210", "name": "Rahul"}])
    assert r.status_code == 200 and r.json()["created"] == 1

    csv_bytes = "phone,name,company,city\n+911234567890,Asha,Acme,Pune\n,missing,x,y\n".encode()
    r = client.post(
        "/api/v1/leads/import",
        files={"file": ("leads.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert r.json() == {"created": 1, "skipped_no_phone": 1}

    leads = client.get("/api/v1/leads").json()
    assert len(leads) == 2
    asha = next(l for l in leads if l["name"] == "Asha")
    assert asha["custom"] == {"city": "Pune"}  # unknown columns preserved

    r = client.patch(f"/api/v1/leads/{asha['id']}", json={"status": "interested"})
    assert r.json()["status"] == "interested"


def test_campaign_flow(client):
    r = client.post(
        "/api/v1/campaigns",
        json={"name": "Solar July", "agent_id": "lead-gen-demo", "goal": "qualify"},
    )
    assert r.status_code == 200
    camp = r.json()
    assert camp["status"] == "draft"

    lead_id = client.post("/api/v1/leads", json=[{"phone": "+919000000001"}]).json()["ids"][0]
    r = client.post(f"/api/v1/campaigns/{camp['id']}/leads", json=[lead_id])
    assert r.json()["attached"] == 1

    r = client.post(f"/api/v1/campaigns/{camp['id']}/start")
    assert r.json()["status"] == "running"
    r = client.post(f"/api/v1/campaigns/{camp['id']}/pause")
    assert r.json()["status"] == "paused"

    detail = client.get(f"/api/v1/campaigns/{camp['id']}").json()
    assert detail["stats"]["leads_total"] == 1

    # unknown agent rejected
    r = client.post("/api/v1/campaigns", json={"name": "x", "agent_id": "nope"})
    assert r.status_code == 400


def test_dnc_and_stats(client):
    client.post("/api/v1/dnc?phone=%2B911112223334&reason=asked")
    entries = client.get("/api/v1/dnc").json()
    assert entries[0]["phone"] == "+911112223334"
    client.delete("/api/v1/dnc/%2B911112223334")
    assert client.get("/api/v1/dnc").json() == []

    stats = client.get("/api/v1/stats").json()
    assert "calls_total" in stats and "leads_total" in stats


def test_api_key_enforced(fresh_db, monkeypatch):
    from outreach.config import get_settings

    monkeypatch.setenv("OUTREACH_API_KEY", "sekret")
    get_settings.cache_clear()
    from outreach.server import app

    with TestClient(app) as c:
        assert c.get("/api/v1/stats").status_code == 401
        assert c.get("/api/v1/stats", headers={"X-API-Key": "sekret"}).status_code == 200
    get_settings.cache_clear()


def test_dashboard_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Outreach" in r.text
