import pytest

from appointment_config import ConfigError, load_config

REQUIRED = {
    "ACUITY_USER_ID": "uid",
    "ACUITY_API_KEY": "key",
    "TELEGRAM_BOT_TOKEN": "tok",
    "TELEGRAM_CHAT_ID": "chat",
}


def set_required(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)


def test_load_config_with_defaults(monkeypatch):
    set_required(monkeypatch)
    for k in ("POLL_WINDOW_DAYS", "REMINDER_THRESHOLDS_MINUTES", "APPOINTMENT_FAILURE_ALERT_THRESHOLD", "APPOINTMENT_STATE_DB_PATH", "APPOINTMENT_DRY_RUN"):
        monkeypatch.delenv(k, raising=False)

    cfg = load_config()

    assert cfg.acuity_user_id == "uid"
    assert cfg.poll_window_days == 7
    assert cfg.reminder_thresholds_minutes == [1440, 60]
    assert cfg.failure_alert_threshold == 3
    assert cfg.state_db_path == "state.db"
    assert cfg.dry_run is False


def test_load_config_missing_required_raises(monkeypatch):
    for k in REQUIRED:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ConfigError, match="ACUITY_USER_ID"):
        load_config()


def test_load_config_parses_custom_thresholds(monkeypatch):
    set_required(monkeypatch)
    monkeypatch.setenv("REMINDER_THRESHOLDS_MINUTES", "2880, 120,15")
    cfg = load_config()
    assert cfg.reminder_thresholds_minutes == [2880, 120, 15]


def test_load_config_rejects_non_integer_threshold(monkeypatch):
    set_required(monkeypatch)
    monkeypatch.setenv("REMINDER_THRESHOLDS_MINUTES", "abc")
    with pytest.raises(ConfigError):
        load_config()


def test_load_config_dry_run_flag(monkeypatch):
    set_required(monkeypatch)
    monkeypatch.setenv("APPOINTMENT_DRY_RUN", "1")
    cfg = load_config()
    assert cfg.dry_run is True
