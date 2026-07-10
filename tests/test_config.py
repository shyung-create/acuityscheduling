import os

import pytest

import config as config_mod


def _write(tmp_path, content):
    path = tmp_path / "config.yaml"
    path.write_text(content)
    return str(path)


def test_load_config_minimal(tmp_path):
    path = _write(tmp_path, 'target_dates: ["2026-09-26"]\n')
    cfg = config_mod.load_config(path)
    assert cfg.target_dates == ["2026-09-26"]
    assert cfg.timezone == "America/Los_Angeles"
    assert cfg.acuity.owner == "dc1e29cb"
    assert cfg.poll.hot_minutes == 5


def test_load_config_allows_empty_target_dates(tmp_path):
    # The dashboard starts with zero watched dates and adds them at runtime,
    # persisted into state.json rather than config.yaml.
    path = _write(tmp_path, "timezone: America/Los_Angeles\n")
    cfg = config_mod.load_config(path)
    assert cfg.target_dates == []


def test_load_config_rejects_non_list_target_dates(tmp_path):
    path = _write(tmp_path, 'target_dates: "2026-09-26"\n')
    with pytest.raises(ValueError):
        config_mod.load_config(path)


def test_load_config_overrides(tmp_path):
    path = _write(
        tmp_path,
        """
target_dates: ["2026-09-26", "2026-10-03"]
time_window:
  start: "09:00"
  end: "17:00"
poll:
  far_hours: 12
  hot_minutes: 3
channels:
  telegram: false
dry_run: true
""",
    )
    cfg = config_mod.load_config(path)
    assert cfg.target_dates == ["2026-09-26", "2026-10-03"]
    assert cfg.time_window.start == "09:00"
    assert cfg.time_window.enabled is True
    assert cfg.poll.far_hours == 12
    assert cfg.poll.hot_minutes == 3
    assert cfg.channels.telegram is False
    assert cfg.channels.email is True
    assert cfg.dry_run is True


def test_load_secrets_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    secrets = config_mod.load_secrets()
    assert secrets.telegram_bot_token == "abc123"
    assert secrets.telegram_chat_id == "42"
    assert secrets.smtp_host is None


def test_time_window_disabled_when_unset():
    tw = config_mod.TimeWindow()
    assert tw.enabled is False


def test_load_secrets_defaults_smtp_port_when_unset(monkeypatch):
    monkeypatch.delenv("SMTP_PORT", raising=False)
    assert config_mod.load_secrets().smtp_port == 587


def test_load_secrets_defaults_smtp_port_when_empty_string(monkeypatch):
    # GitHub Actions' `env:` block sets SMTP_PORT="" (not unset) when the
    # repo secret isn't configured -- this must not crash with a ValueError.
    monkeypatch.setenv("SMTP_PORT", "")
    assert config_mod.load_secrets().smtp_port == 587


def test_load_secrets_respects_explicit_smtp_port(monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "2525")
    assert config_mod.load_secrets().smtp_port == 2525


# --- per-date time_window overrides ---

def test_target_dates_plain_strings_use_default_time_window(tmp_path):
    path = _write(
        tmp_path,
        """
target_dates:
  - "2026-08-28"
time_window:
  start: "09:00"
  end: "18:00"
""",
    )
    cfg = config_mod.load_config(path)
    assert cfg.target_dates == ["2026-08-28"]
    assert cfg.time_windows == {}
    window = cfg.time_window_for("2026-08-28")
    assert (window.start, window.end) == ("09:00", "18:00")


def test_target_dates_mapping_entry_gets_own_time_window(tmp_path):
    path = _write(
        tmp_path,
        """
target_dates:
  - "2026-08-28"
  - date: "2026-09-18"
    time_window:
      start: "17:00"
      end: "18:00"
time_window: {}
""",
    )
    cfg = config_mod.load_config(path)
    assert cfg.target_dates == ["2026-08-28", "2026-09-18"]

    aug28 = cfg.time_window_for("2026-08-28")
    assert aug28.enabled is False  # falls back to the empty global default

    sep18 = cfg.time_window_for("2026-09-18")
    assert (sep18.start, sep18.end) == ("17:00", "18:00")
    assert sep18.enabled is True


def test_target_dates_mapping_entry_without_time_window_uses_default(tmp_path):
    path = _write(
        tmp_path,
        """
target_dates:
  - date: "2026-08-28"
time_window:
  start: "09:00"
  end: "18:00"
""",
    )
    cfg = config_mod.load_config(path)
    window = cfg.time_window_for("2026-08-28")
    assert (window.start, window.end) == ("09:00", "18:00")


def test_target_dates_mapping_entry_missing_date_raises(tmp_path):
    path = _write(tmp_path, 'target_dates:\n  - time_window: {start: "17:00", end: "18:00"}\n')
    with pytest.raises(ValueError):
        config_mod.load_config(path)


def test_target_dates_entry_wrong_type_raises(tmp_path):
    path = _write(tmp_path, "target_dates:\n  - 20260828\n")
    with pytest.raises(ValueError):
        config_mod.load_config(path)


def test_time_window_for_unknown_date_uses_default():
    cfg = config_mod.Config(target_dates=["2026-08-28"], time_window=config_mod.TimeWindow(start="09:00", end="18:00"))
    window = cfg.time_window_for("2099-01-01")
    assert (window.start, window.end) == ("09:00", "18:00")
