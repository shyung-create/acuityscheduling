from datetime import date, timedelta

import pytest

import monitor
import state_store
from config import Config


def make_cfg(target_dates):
    return Config(target_dates=target_dates)


def test_effective_target_dates_unions_config_and_state():
    cfg = make_cfg(["2026-09-26"])
    state = state_store.default_state()
    state_store.get_target_state(state, "2026-10-03")  # added at runtime, not in config
    assert monitor.effective_target_dates(cfg, state) == ["2026-09-26", "2026-10-03"]


def test_effective_target_dates_dedups():
    cfg = make_cfg(["2026-09-26"])
    state = state_store.default_state()
    state_store.get_target_state(state, "2026-09-26")
    assert monitor.effective_target_dates(cfg, state) == ["2026-09-26"]


def test_add_target_date_creates_state_entry():
    state = state_store.default_state()
    future = (date.today() + timedelta(days=90)).isoformat()
    tstate = monitor.add_target_date(state, future)
    assert future in state["targets"]
    assert tstate["dismissed"] is False


def test_add_target_date_rejects_bad_format():
    state = state_store.default_state()
    with pytest.raises(ValueError):
        monitor.add_target_date(state, "not-a-date")


def test_add_target_date_rejects_past_date():
    state = state_store.default_state()
    past = (date.today() - timedelta(days=1)).isoformat()
    with pytest.raises(ValueError):
        monitor.add_target_date(state, past)


def test_add_target_date_undismisses_existing():
    state = state_store.default_state()
    future = (date.today() + timedelta(days=90)).isoformat()
    tstate = state_store.get_target_state(state, future)
    tstate["dismissed"] = True
    monitor.add_target_date(state, future)
    assert state["targets"][future]["dismissed"] is False


def test_remove_target_date_deletes_entry():
    state = state_store.default_state()
    state_store.get_target_state(state, "2026-09-26")
    assert monitor.remove_target_date(state, "2026-09-26") is True
    assert "2026-09-26" not in state["targets"]


def test_remove_target_date_missing_returns_false():
    state = state_store.default_state()
    assert monitor.remove_target_date(state, "2026-09-26") is False
