import json
import os
from datetime import datetime, timedelta, timezone

import monitor

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def test_write_heartbeat_creates_file_with_expected_shape(tmp_path):
    path = str(tmp_path / "heartbeat.txt")
    monitor.write_heartbeat(path, NOW, sleep_seconds=300)

    with open(path) as fh:
        data = json.load(fh)

    assert data["last_poll"] == NOW.isoformat()
    last_poll = datetime.fromisoformat(data["last_poll"])
    next_expected_by = datetime.fromisoformat(data["next_expected_by"])
    assert next_expected_by > last_poll + timedelta(seconds=300)  # sleep_seconds plus grace


def test_write_heartbeat_grace_period_is_additive(tmp_path):
    path = str(tmp_path / "heartbeat.txt")
    monitor.write_heartbeat(path, NOW, sleep_seconds=600, grace_minutes=15)

    with open(path) as fh:
        data = json.load(fh)
    next_expected_by = datetime.fromisoformat(data["next_expected_by"])
    assert next_expected_by == NOW + timedelta(seconds=600) + timedelta(minutes=15)


def test_write_heartbeat_far_phase_gap_does_not_look_stale(tmp_path):
    # Regression guard: a 6-hour adaptive "far" phase gap must not make
    # next_expected_by look overdue immediately after writing.
    path = str(tmp_path / "heartbeat.txt")
    six_hours = 6 * 3600
    monitor.write_heartbeat(path, NOW, sleep_seconds=six_hours)

    with open(path) as fh:
        data = json.load(fh)
    next_expected_by = datetime.fromisoformat(data["next_expected_by"])
    assert next_expected_by > NOW + timedelta(hours=6)


def test_write_heartbeat_leaves_no_tmp_file_on_success(tmp_path):
    path = str(tmp_path / "heartbeat.txt")
    monitor.write_heartbeat(path, NOW, sleep_seconds=60)
    assert not os.path.exists(path + ".tmp")


def test_write_heartbeat_overwrites_existing_file(tmp_path):
    path = str(tmp_path / "heartbeat.txt")
    monitor.write_heartbeat(path, NOW, sleep_seconds=60)
    monitor.write_heartbeat(path, NOW + timedelta(minutes=10), sleep_seconds=120)

    with open(path) as fh:
        data = json.load(fh)
    assert data["last_poll"] == (NOW + timedelta(minutes=10)).isoformat()
