import json
from datetime import datetime, timedelta, timezone

import heartbeat_check

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def write_heartbeat_file(path, last_poll, next_expected_by):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"last_poll": last_poll.isoformat(), "next_expected_by": next_expected_by.isoformat()}, fh)


def configure(monkeypatch, tmp_path, next_expected_by=None, last_poll=None, missing=False, token="tok", chat_id="chat"):
    heartbeat_path = tmp_path / "heartbeat.txt"
    if not missing:
        lp = last_poll if last_poll is not None else NOW - timedelta(minutes=5)
        neb = next_expected_by if next_expected_by is not None else NOW + timedelta(minutes=15)
        write_heartbeat_file(heartbeat_path, lp, neb)

    monkeypatch.setattr(heartbeat_check, "HEARTBEAT_FILE_PATH", str(heartbeat_path))
    monkeypatch.setattr(heartbeat_check, "ALERT_SENTINEL_PATH", str(heartbeat_path) + ".alerted")
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


def test_read_heartbeat_missing_file_returns_none_none(tmp_path):
    assert heartbeat_check.read_heartbeat(str(tmp_path / "nope.txt")) == (None, None)


def test_read_heartbeat_corrupt_file_returns_none_none(tmp_path):
    path = tmp_path / "heartbeat.txt"
    path.write_text("not json")
    assert heartbeat_check.read_heartbeat(str(path)) == (None, None)


def test_read_heartbeat_missing_keys_returns_none_none(tmp_path):
    path = tmp_path / "heartbeat.txt"
    path.write_text(json.dumps({"last_poll": NOW.isoformat()}))  # no next_expected_by
    assert heartbeat_check.read_heartbeat(str(path)) == (None, None)


def test_read_heartbeat_valid_roundtrip(tmp_path):
    path = tmp_path / "heartbeat.txt"
    neb = NOW + timedelta(minutes=15)
    write_heartbeat_file(path, NOW, neb)
    last_poll, next_expected_by = heartbeat_check.read_heartbeat(str(path))
    assert last_poll == NOW
    assert next_expected_by == neb


def test_main_ok_when_not_yet_due(tmp_path, monkeypatch):
    freeze_now(monkeypatch)
    sent = capture_sent(monkeypatch)
    configure(monkeypatch, tmp_path, next_expected_by=NOW + timedelta(hours=5))  # e.g. far-phase gap

    rc = heartbeat_check.main()

    assert rc == 0
    assert sent == []


def test_main_alerts_when_past_next_expected_by(tmp_path, monkeypatch):
    freeze_now(monkeypatch)
    sent = capture_sent(monkeypatch)
    configure(monkeypatch, tmp_path, next_expected_by=NOW - timedelta(minutes=1))

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
    configure(monkeypatch, tmp_path, next_expected_by=NOW - timedelta(minutes=1))

    heartbeat_check.main()
    assert len(sent) == 1

    heartbeat_check.main()
    assert len(sent) == 1


def test_main_clears_sentinel_and_realerts_after_recovery(tmp_path, monkeypatch):
    freeze_now(monkeypatch)
    sent = capture_sent(monkeypatch)
    heartbeat_path = configure(monkeypatch, tmp_path, next_expected_by=NOW - timedelta(minutes=1))

    heartbeat_check.main()
    assert len(sent) == 1
    assert __import__("os").path.exists(heartbeat_check.ALERT_SENTINEL_PATH)

    write_heartbeat_file(heartbeat_path, NOW, NOW + timedelta(minutes=15))
    heartbeat_check.main()
    assert len(sent) == 1
    assert not __import__("os").path.exists(heartbeat_check.ALERT_SENTINEL_PATH)

    write_heartbeat_file(heartbeat_path, NOW - timedelta(hours=1), NOW - timedelta(minutes=1))
    heartbeat_check.main()
    assert len(sent) == 2


def test_main_returns_1_when_telegram_vars_missing(tmp_path, monkeypatch):
    freeze_now(monkeypatch)
    capture_sent(monkeypatch)
    configure(monkeypatch, tmp_path, next_expected_by=NOW - timedelta(minutes=1), token=None, chat_id=None)

    rc = heartbeat_check.main()

    assert rc == 1


def test_main_returns_nonzero_when_send_fails(tmp_path, monkeypatch):
    freeze_now(monkeypatch)
    configure(monkeypatch, tmp_path, next_expected_by=NOW - timedelta(minutes=1))
    monkeypatch.setattr(heartbeat_check, "send_telegram", lambda *a, **k: False)

    rc = heartbeat_check.main()

    assert rc == 1
