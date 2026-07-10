import os
from datetime import datetime, timedelta, timezone

import state_store


def test_default_state_shape():
    state = state_store.default_state()
    assert state["targets"] == {}
    assert state["version"] == state_store.STATE_VERSION


def test_get_target_state_creates_default():
    state = state_store.default_state()
    tstate = state_store.get_target_state(state, "2026-09-26")
    assert tstate["seen_slot_times"] == []
    assert tstate["window_open_alerted"] is False
    assert "2026-09-26" in state["targets"]


def test_get_target_state_is_idempotent():
    state = state_store.default_state()
    a = state_store.get_target_state(state, "2026-09-26")
    a["seen_slot_times"] = ["x"]
    b = state_store.get_target_state(state, "2026-09-26")
    assert b["seen_slot_times"] == ["x"]


def test_diff_slot_times_new_and_removed():
    previous = {"2026-09-26T10:00:00-0700", "2026-09-26T10:45:00-0700"}
    current = {"2026-09-26T10:45:00-0700", "2026-09-26T14:30:00-0700"}
    new, removed = state_store.diff_slot_times(previous, current)
    assert new == {"2026-09-26T14:30:00-0700"}
    assert removed == {"2026-09-26T10:00:00-0700"}


def test_diff_slot_times_no_change():
    times = {"2026-09-26T10:00:00-0700"}
    new, removed = state_store.diff_slot_times(times, times)
    assert new == set()
    assert removed == set()


def test_diff_slot_times_first_appearance():
    new, removed = state_store.diff_slot_times(set(), {"2026-09-26T10:00:00-0700"})
    assert new == {"2026-09-26T10:00:00-0700"}
    assert removed == set()


def test_can_alert_true_when_never_alerted():
    tstate = state_store._default_target_state()
    now = datetime.now(timezone.utc)
    assert state_store.can_alert(tstate, "telegram", now, 600) is True


def test_can_alert_false_within_rate_limit():
    tstate = state_store._default_target_state()
    now = datetime.now(timezone.utc)
    state_store.record_alert(tstate, "telegram", now)
    later = now + timedelta(minutes=5)
    assert state_store.can_alert(tstate, "telegram", later, 600) is False


def test_can_alert_true_after_rate_limit_expires():
    tstate = state_store._default_target_state()
    now = datetime.now(timezone.utc)
    state_store.record_alert(tstate, "telegram", now)
    later = now + timedelta(minutes=11)
    assert state_store.can_alert(tstate, "telegram", later, 600) is True


def test_can_alert_channels_independent():
    tstate = state_store._default_target_state()
    now = datetime.now(timezone.utc)
    state_store.record_alert(tstate, "telegram", now)
    assert state_store.can_alert(tstate, "email", now, 600) is True


def test_save_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    state = state_store.default_state()
    tstate = state_store.get_target_state(state, "2026-09-26")
    tstate["seen_slot_times"] = ["2026-09-26T10:00:00-0700"]
    state_store.save_state(path, state)

    loaded = state_store.load_state(path)
    assert loaded["targets"]["2026-09-26"]["seen_slot_times"] == ["2026-09-26T10:00:00-0700"]


def test_load_state_missing_file_returns_default(tmp_path):
    path = str(tmp_path / "does_not_exist.json")
    state = state_store.load_state(path)
    assert state == state_store.default_state()


def test_load_state_corrupt_json_returns_default(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json")
    state = state_store.load_state(str(path))
    assert state["targets"] == {}
