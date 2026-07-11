from datetime import datetime, timedelta, timezone

import pytest

import heartbeat_check

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def configure(monkeypatch, tmp_path, stale_minutes=20, fresh_age_minutes=None, missing=False, token="tok", chat_id="chat"):
    heartbeat_path = tmp_path / "heartbeat.txt"
    if not missing:
        age = fresh_age_minutes if fresh_age_minutes is not None else 5
        heartbeat_path.write_text((NOW - timedelta(minutes=age)).isoformat())

    monkeypatch.setattr(heartbeat_check, "HEARTBEAT_FILE_PATH", str(heartbeat_path))
    monkeypatch.setattr(heartbeat_check, "ALERT_SENTINEL_PATH", str(heartbeat_path) + ".alerted")
    monkeypatch.setattr(heartbeat_check, "STALE_MINUTES", stale_minutes)
    monkeypatch.setattr(heartbeat_check, "TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setattr(heartbeat_check, "TELEGRAM_CHAT_ID", chat_id)
    return heartbeat_path


def capture_sent(monkeypatch):
    sent = []

    def fake_send(token, chat_id, text, timeout=10):
        sent.append(text)
        return True

    monkeypatch.setattr(heartbeat_check, "send_telegram", fake_send)
    return sent


def freeze_now(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(heartbeat_check, "datetime", FrozenDatetime)


def test_read_heartbeat_missing_file_returns_none(tmp_path):
    assert heartbeat_check.read_heartbeat(str(tmp_path / "nope.txt")) is None


def test_read_heartbeat_corrupt_file_returns_none(tmp_path):
    path = tmp_path / "heartbeat.txt"
    path.write_text("not a timestamp")
    assert heartbeat_check.read_heartbeat(str(path)) is None


def test_read_heartbeat_valid_roundtrip(tmp_path):
    path = tmp_path / "heartbeat.txt"
    path.write_text(NOW.isoformat())
    assert heartbeat_check.read_heartbeat(str(path)) == NOW


def test_main_ok_when_fresh_sends_nothing(tmp_path, monkeypatch):
    freeze_now(monkeypatch)
    sent = capture_sent(monkeypatch)
    configure(monkeypatch, tmp_path, stale_minutes=20, fresh_age_minutes=5)

    rc = heartbeat_check.main()

    assert rc == 0
    assert sent == []


def test_main_alerts_when_stale(tmp_path, monkeypatch):
    freeze_now(monkeypatch)
    sent = capture_sent(monkeypatch)
    configure(monkeypatch, tmp_path, stale_minutes=20, fresh_age_minutes=45)

    rc = heartbeat_check.main()

    assert rc == 0
    assert len(sent) == 1
    assert "stuck" in sent[0]


def test_main_alerts_when_heartbeat_file_missing(tmp_path, monkeypatch):
    freeze_now(monkeypatch)
    sent = capture_sent(monkeypatch)
    configure(monkeypatch, tmp_path, missing=True)

    rc = heartbeat_check.main()

    assert rc == 0
    assert len(sent) == 1
    assert "missing" in sent[0]


def test_main_does_not_double_alert_while_still_stale(tmp_path, monkeypatch):
    freeze_now(monkeypatch)
    sent = capture_sent(monkeypatch)
    configure(monkeypatch, tmp_path, stale_minutes=20, fresh_age_minutes=45)

    heartbeat_check.main()
    assert len(sent) == 1

    # Still stale on the next check -- must not send a second alert.
    heartbeat_check.main()
    assert len(sent) == 1


def test_main_clears_sentinel_and_realerts_after_recovery(tmp_path, monkeypatch):
    freeze_now(monkeypatch)
    sent = capture_sent(monkeypatch)
    heartbeat_path = configure(monkeypatch, tmp_path, stale_minutes=20, fresh_age_minutes=45)

    heartbeat_check.main()
    assert len(sent) == 1
    assert __import__("os").path.exists(heartbeat_check.ALERT_SENTINEL_PATH)

    # Heartbeat comes back fresh -- sentinel should clear, no new alert.
    heartbeat_path.write_text(NOW.isoformat())
    heartbeat_check.main()
    assert len(sent) == 1
    assert not __import__("os").path.exists(heartbeat_check.ALERT_SENTINEL_PATH)

    # Goes stale again -- should alert again since the sentinel was cleared.
    heartbeat_path.write_text((NOW - timedelta(minutes=45)).isoformat())
    heartbeat_check.main()
    assert len(sent) == 2


def test_main_returns_1_when_telegram_vars_missing(tmp_path, monkeypatch):
    freeze_now(monkeypatch)
    capture_sent(monkeypatch)
    configure(monkeypatch, tmp_path, stale_minutes=20, fresh_age_minutes=45, token=None, chat_id=None)

    rc = heartbeat_check.main()

    assert rc == 1


def test_main_returns_nonzero_when_send_fails(tmp_path, monkeypatch):
    freeze_now(monkeypatch)
    configure(monkeypatch, tmp_path, stale_minutes=20, fresh_age_minutes=45)
    monkeypatch.setattr(heartbeat_check, "send_telegram", lambda *a, **k: False)

    rc = heartbeat_check.main()

    assert rc == 1
