from datetime import date, timedelta

import pytest

import monitor
import state_store
from config import Config, TimeWindow


def make_cfg(target_dates, time_window=None, time_windows=None):
    return Config(
        target_dates=target_dates,
        time_window=time_window or TimeWindow(),
        time_windows=time_windows or {},
    )


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


# --- per-date time_window overrides (dashboard-settable, stored in state.json) ---

def test_resolve_time_window_falls_back_to_config_default_when_no_override():
    cfg = make_cfg(["2026-08-28"], time_window=TimeWindow(start="09:00", end="18:00"))
    state = state_store.default_state()
    tstate = state_store.get_target_state(state, "2026-08-28")
    window = monitor.resolve_time_window(cfg, tstate, "2026-08-28")
    assert (window.start, window.end) == ("09:00", "18:00")


def test_resolve_time_window_falls_back_to_config_per_date_override():
    cfg = make_cfg(["2026-09-18"], time_windows={"2026-09-18": TimeWindow(start="17:00", end="18:00")})
    state = state_store.default_state()
    tstate = state_store.get_target_state(state, "2026-09-18")
    window = monitor.resolve_time_window(cfg, tstate, "2026-09-18")
    assert (window.start, window.end) == ("17:00", "18:00")


def test_add_target_date_with_time_window_overrides_config_default():
    cfg = make_cfg(["2026-08-28"], time_window=TimeWindow(start="09:00", end="18:00"))
    state = state_store.default_state()
    monitor.add_target_date(state, "2026-08-28", cfg, time_window={"start": "17:00", "end": "18:00"})
    tstate = state_store.get_target_state(state, "2026-08-28")
    window = monitor.resolve_time_window(cfg, tstate, "2026-08-28")
    assert (window.start, window.end) == ("17:00", "18:00")


def test_add_target_date_with_empty_time_window_means_any_time():
    # An explicit {} override ("any time") must NOT be treated as "no
    # override" just because an empty dict is falsy -- it should win over a
    # non-empty config default.
    cfg = make_cfg(["2026-08-28"], time_window=TimeWindow(start="09:00", end="18:00"))
    state = state_store.default_state()
    monitor.add_target_date(state, "2026-08-28", cfg, time_window={})
    tstate = state_store.get_target_state(state, "2026-08-28")
    window = monitor.resolve_time_window(cfg, tstate, "2026-08-28")
    assert window.enabled is False


def test_add_target_date_without_time_window_leaves_existing_override_untouched():
    cfg = make_cfg(["2026-08-28"])
    state = state_store.default_state()
    monitor.add_target_date(state, "2026-08-28", cfg, time_window={"start": "17:00", "end": "18:00"})
    monitor.add_target_date(state, "2026-08-28", cfg)  # e.g. a later undismiss call, no time_window passed
    tstate = state_store.get_target_state(state, "2026-08-28")
    window = monitor.resolve_time_window(cfg, tstate, "2026-08-28")
    assert (window.start, window.end) == ("17:00", "18:00")


# --- poll-interval override (dashboard-settable, stored in state.json) ---

def test_get_effective_fixed_minutes_falls_back_to_config():
    from config import PollConfig

    cfg = Config(target_dates=[], poll=PollConfig(fixed_minutes=5))
    state = state_store.default_state()
    assert monitor.get_effective_fixed_minutes(cfg, state) == 5


def test_get_effective_fixed_minutes_none_when_neither_set():
    cfg = Config(target_dates=[])
    state = state_store.default_state()
    assert monitor.get_effective_fixed_minutes(cfg, state) is None


def test_set_fixed_minutes_override_wins_over_config():
    from config import PollConfig

    cfg = Config(target_dates=[], poll=PollConfig(fixed_minutes=5))
    state = state_store.default_state()
    monitor.set_fixed_minutes_override(state, 10)
    assert monitor.get_effective_fixed_minutes(cfg, state) == 10


def test_set_fixed_minutes_override_to_none_forces_adaptive_even_if_config_has_fixed():
    from config import PollConfig

    cfg = Config(target_dates=[], poll=PollConfig(fixed_minutes=5))
    state = state_store.default_state()
    monitor.set_fixed_minutes_override(state, None)  # explicit "go back to adaptive"
    assert monitor.get_effective_fixed_minutes(cfg, state) is None
