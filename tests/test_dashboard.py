import json
import os
from datetime import date, timedelta

import pytest

import dashboard
import monitor


class FakeClient:
    """No network: month/times fixed so status/poll-now tests are deterministic."""

    def get_month(self, year_month=None):
        return {}

    def get_times(self, date_str):
        return []


@pytest.fixture()
def client(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
target_dates: []
state_file: "{tmp_path}/state.json"
log_file: "{tmp_path}/monitor.log"
dry_run: true
channels:
  telegram: false
  email: false
"""
    )
    monkeypatch.setattr(monitor, "build_client", lambda engine, cfg: FakeClient())
    dashboard.init_app(str(config_path), "requests", True, "127.0.0.1", False)
    # Background thread deliberately not started -- tests drive polling manually.
    with dashboard.app.test_client() as c:
        yield c


def future_date(days=90):
    return (date.today() + timedelta(days=days)).isoformat()


def test_status_empty_initially(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["targets"] == []


def test_add_single_date(client):
    d = future_date()
    resp = client.post("/api/targets", json={"date": d})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["added"] == [d]

    status = client.get("/api/status").get_json()
    assert [t["date"] for t in status["targets"]] == [d]
    assert status["targets"][0]["badge"] == "watching"


def test_add_bulk_dates(client):
    d1, d2 = future_date(60), future_date(90)
    resp = client.post("/api/targets", json={"dates": f"{d1},\n{d2}"})
    data = resp.get_json()
    assert sorted(data["added"]) == sorted([d1, d2])


def test_add_invalid_date_reports_error(client):
    resp = client.post("/api/targets", json={"date": "not-a-date"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert "not-a-date" in data["errors"]


def test_add_past_date_reports_error(client):
    past = (date.today() - timedelta(days=5)).isoformat()
    resp = client.post("/api/targets", json={"date": past})
    assert resp.status_code == 400
    assert past in resp.get_json()["errors"]


def test_remove_target(client):
    d = future_date()
    client.post("/api/targets", json={"date": d})
    resp = client.delete(f"/api/targets/{d}")
    assert resp.status_code == 200
    assert resp.get_json()["removed"] is True

    status = client.get("/api/status").get_json()
    assert status["targets"] == []


def test_remove_missing_target_404(client):
    resp = client.delete("/api/targets/2099-01-01")
    assert resp.status_code == 404


def test_dismiss_and_undismiss(client):
    d = future_date()
    client.post("/api/targets", json={"date": d})

    client.post(f"/api/targets/{d}/dismiss")
    status = client.get("/api/status").get_json()
    assert status["targets"][0]["badge"] == "dismissed"

    client.post(f"/api/targets/{d}/undismiss")
    status = client.get("/api/status").get_json()
    assert status["targets"][0]["badge"] == "watching"


def test_poll_now_runs_without_error(client):
    d = future_date()
    client.post("/api/targets", json={"date": d})
    resp = client.post("/api/poll-now")
    assert resp.status_code == 200
    assert resp.get_json()["polled"] is True


def test_test_notification_reports_not_configured_when_channels_disabled(client):
    resp = client.post("/api/test-notification")
    assert resp.status_code == 200
    # channels.telegram/email are false in the fixture config, so neither key appears
    assert resp.get_json() == {}


def test_index_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Haircut Availability Dashboard" in resp.data or b"Availability Dashboard" in resp.data
