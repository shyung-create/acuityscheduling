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
