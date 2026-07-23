from datetime import date, datetime, timezone

import config as config_mod
import monitor
import state_store
from config import Config, PollConfig


def make_cfg(target_dates, poll=None):
    return Config(target_dates=target_dates, poll=poll or PollConfig())


def test_compute_next_sleep_seconds_uses_adaptive_schedule_by_default():
    cfg = make_cfg(["2026-09-26"], PollConfig(far_hours=6))
    state = state_store.default_state()
    now = datetime.now(timezone.utc)
    seconds = monitor.compute_next_sleep_seconds(cfg, state, now)
    # far phase, target is months away -- +/-10% jitter is applied, so check a range
    assert 6 * 3600 * 0.85 <= seconds <= 6 * 3600 * 1.15


def test_compute_next_sleep_seconds_fixed_minutes_overrides_adaptive_schedule():
    # Even though the target is far away (would normally be the 6h "far"
    # phase), a configured fixed_minutes must win outright.
    cfg = make_cfg(["2026-09-26"], PollConfig(far_hours=6, fixed_minutes=5))
    state = state_store.default_state()
    now = datetime.now(timezone.utc)
    assert monitor.compute_next_sleep_seconds(cfg, state, now) == 5 * 60


def test_compute_next_sleep_seconds_fixed_minutes_ignores_target_state():
    # Fixed interval should apply globally -- not per-target phase logic --
    # regardless of whether a slot was already found, the window opened, etc.
    cfg = make_cfg(["2026-09-26"], PollConfig(fixed_minutes=10))
    state = state_store.default_state()
    tstate = state_store.get_target_state(state, "2026-09-26")
    tstate["seen_slot_times"] = ["2026-09-26T10:00:00-0700"]
    tstate["measured_open_date"] = date.today().isoformat()
    now = datetime.now(timezone.utc)
    assert monitor.compute_next_sleep_seconds(cfg, state, now) == 10 * 60


def test_compute_next_sleep_seconds_fixed_minutes_respects_floor():
    cfg = make_cfg(["2026-09-26"], PollConfig(fixed_minutes=0.1, min_minutes=2))
    state = state_store.default_state()
    now = datetime.now(timezone.utc)
    assert monitor.compute_next_sleep_seconds(cfg, state, now) == 2 * 60


def test_compute_next_sleep_seconds_fixed_minutes_with_no_targets_still_applies():
    cfg = make_cfg([], PollConfig(fixed_minutes=15))
    state = state_store.default_state()
    now = datetime.now(timezone.utc)
    assert monitor.compute_next_sleep_seconds(cfg, state, now) == 15 * 60


# --- CLI wiring: --interval sets cfg.poll.fixed_minutes ---

def test_cli_interval_flag_sets_fixed_minutes(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
target_dates: []
state_file: "{tmp_path.as_posix()}/state.json"
log_file: "{tmp_path.as_posix()}/monitor.log"
dry_run: true
channels:
  telegram: false
  email: false
"""
    )

    captured = {}
    real_load_config = config_mod.load_config

    def spy_load_config(path):
        cfg = real_load_config(path)
        captured["cfg"] = cfg
        return cfg

    monkeypatch.setattr(monitor.config_mod, "load_config", spy_load_config)
    monkeypatch.setattr(monitor, "build_client", lambda engine, cfg: None)

    monitor.main(["--once", "--config", str(config_path), "--interval", "7"])

    assert captured["cfg"].poll.fixed_minutes == 7


def test_cli_without_interval_leaves_fixed_minutes_unset(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
target_dates: []
state_file: "{tmp_path.as_posix()}/state.json"
log_file: "{tmp_path.as_posix()}/monitor.log"
dry_run: true
channels:
  telegram: false
  email: false
"""
    )

    captured = {}
    real_load_config = config_mod.load_config

    def spy_load_config(path):
        cfg = real_load_config(path)
        captured["cfg"] = cfg
        return cfg

    monkeypatch.setattr(monitor.config_mod, "load_config", spy_load_config)
    monkeypatch.setattr(monitor, "build_client", lambda engine, cfg: None)

    monitor.main(["--once", "--config", str(config_path)])

    assert captured["cfg"].poll.fixed_minutes is None
