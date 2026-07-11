from datetime import datetime, timedelta, timezone

import appointment_monitor
import appointment_store
import notifications
from appointment_client import STATUS_ACTIVE, AcuityAppointmentsError, Appointment
from appointment_config import AppointmentMonitorConfig
from appointment_diff import NEW_BOOKING

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def make_cfg(**overrides) -> AppointmentMonitorConfig:
    defaults = dict(
        acuity_user_id="uid",
        acuity_api_key="key",
        telegram_bot_token="tok",
        telegram_chat_id="chat",
        poll_window_days=7,
        reminder_thresholds_minutes=[1440, 60],
        failure_alert_threshold=3,
        state_db_path=":memory:",
        dry_run=False,
    )
    defaults.update(overrides)
    return AppointmentMonitorConfig(**defaults)


class FakeClient:
    def __init__(self, appointments=None, error: Exception | None = None):
        self._appointments = appointments or []
        self._error = error
        self.calls = 0

    def get_appointments(self, min_date, max_date):
        self.calls += 1
        if self._error:
            raise self._error
        return self._appointments


def capture_sent(monkeypatch):
    sent = []

    def fake_send_telegram(token, chat_id, text, dry_run=False, timeout=10):
        sent.append(text)
        return True

    monkeypatch.setattr(notifications, "send_telegram", fake_send_telegram)
    return sent


def test_run_cycle_sends_new_booking_alert(tmp_path, monkeypatch):
    sent = capture_sent(monkeypatch)
    conn = appointment_store.connect(str(tmp_path / "state.db"))
    cfg = make_cfg()

    # First-ever cycle on an empty DB is a backfill (see
    # test_run_cycle_first_ever_run_backfills_without_alert) -- seed it with
    # an unrelated appointment first so the DB is non-empty, then introduce a
    # genuinely new appointment on the next cycle.
    seed_appt = Appointment(id="0", status=STATUS_ACTIVE, start_time=NOW + timedelta(days=1), client_name="Seed", service_name="Haircut")
    appointment_monitor.run_cycle(FakeClient(appointments=[seed_appt]), conn, cfg, now=NOW)
    sent.clear()

    appt = Appointment(id="1", status=STATUS_ACTIVE, start_time=NOW + timedelta(days=3), client_name="Jane Doe", service_name="Haircut")
    client = FakeClient(appointments=[seed_appt, appt])

    events = appointment_monitor.run_cycle(client, conn, cfg, now=NOW + timedelta(minutes=5))

    assert [e.alert_type for e in events] == [NEW_BOOKING]
    assert len(sent) == 1
    assert "Jane Doe" in sent[0]


def test_run_cycle_first_ever_run_backfills_without_alert(tmp_path, monkeypatch):
    sent = capture_sent(monkeypatch)
    conn = appointment_store.connect(str(tmp_path / "state.db"))
    appt = Appointment(id="1", status=STATUS_ACTIVE, start_time=NOW + timedelta(days=3), client_name="Jane Doe", service_name="Haircut")
    client = FakeClient(appointments=[appt])
    cfg = make_cfg()

    # Simulate "already backfilled" by seeding the DB directly isn't the point --
    # the monitor itself must detect an empty DB as backfill on the very first call.
    events = appointment_monitor.run_cycle(client, conn, cfg, now=NOW)
    assert events == []
    assert sent == []

    # Second cycle, appointment unchanged -> still nothing.
    events2 = appointment_monitor.run_cycle(client, conn, cfg, now=NOW + timedelta(minutes=5))
    assert events2 == []
    assert sent == []


def test_run_cycle_skips_and_records_failure_on_acuity_error(tmp_path, monkeypatch):
    sent = capture_sent(monkeypatch)
    conn = appointment_store.connect(str(tmp_path / "state.db"))
    client = FakeClient(error=AcuityAppointmentsError("boom"))
    cfg = make_cfg(failure_alert_threshold=3)

    events = appointment_monitor.run_cycle(client, conn, cfg, now=NOW)

    assert events == []
    assert sent == []  # below threshold, no warning yet
    assert appointment_store.get_consecutive_failures(conn) == 1


def test_run_cycle_sends_exactly_one_unhealthy_warning(tmp_path, monkeypatch):
    sent = capture_sent(monkeypatch)
    conn = appointment_store.connect(str(tmp_path / "state.db"))
    client = FakeClient(error=AcuityAppointmentsError("boom"))
    cfg = make_cfg(failure_alert_threshold=3)

    for _ in range(5):
        appointment_monitor.run_cycle(client, conn, cfg, now=NOW)

    unhealthy_msgs = [m for m in sent if "unhealthy" in m]
    assert len(unhealthy_msgs) == 1
    assert appointment_store.get_consecutive_failures(conn) == 5


def test_run_cycle_success_after_failures_resets_streak_and_rearms_warning(tmp_path, monkeypatch):
    sent = capture_sent(monkeypatch)
    conn = appointment_store.connect(str(tmp_path / "state.db"))
    failing_client = FakeClient(error=AcuityAppointmentsError("boom"))
    cfg = make_cfg(failure_alert_threshold=2)

    for _ in range(3):
        appointment_monitor.run_cycle(failing_client, conn, cfg, now=NOW)
    assert len([m for m in sent if "unhealthy" in m]) == 1

    ok_client = FakeClient(appointments=[])
    appointment_monitor.run_cycle(ok_client, conn, cfg, now=NOW + timedelta(minutes=5))
    assert appointment_store.get_consecutive_failures(conn) == 0
    assert appointment_store.get_failure_warning_sent(conn) is False

    # Fails again past threshold -> warns again since the flag was reset.
    for _ in range(3):
        appointment_monitor.run_cycle(failing_client, conn, cfg, now=NOW + timedelta(minutes=10))
    assert len([m for m in sent if "unhealthy" in m]) == 2
