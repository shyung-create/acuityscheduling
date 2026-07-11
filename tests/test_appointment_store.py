from datetime import datetime, timedelta, timezone

import appointment_store
from appointment_client import STATUS_ACTIVE, STATUS_CANCELED, Appointment
from appointment_diff import CANCELLED, NEW_BOOKING, AlertEvent

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def make_appt(appt_id="1", status=STATUS_ACTIVE, start_time=None):
    return Appointment(
        id=appt_id,
        status=status,
        start_time=start_time or (NOW + timedelta(days=1)),
        client_name="Jane Doe",
        service_name="Haircut",
    )


def test_is_empty_true_for_fresh_db(tmp_path):
    conn = appointment_store.connect(str(tmp_path / "state.db"))
    assert appointment_store.is_empty(conn) is True


def test_persist_cycle_then_load_prior_state_roundtrip(tmp_path):
    conn = appointment_store.connect(str(tmp_path / "state.db"))
    appt = make_appt()
    event = AlertEvent(appointment_id="1", alert_type=NEW_BOOKING, appointment=appt)

    appointment_store.persist_cycle(conn, [appt], [event], [], NOW)

    assert appointment_store.is_empty(conn) is False
    prior = appointment_store.load_prior_state(conn)
    assert "1" in prior
    assert prior["1"].status == STATUS_ACTIVE
    assert prior["1"].start_time == appt.start_time
    assert NEW_BOOKING in prior["1"].fired_alerts


def test_persist_cycle_updates_existing_row(tmp_path):
    conn = appointment_store.connect(str(tmp_path / "state.db"))
    appt = make_appt(start_time=NOW + timedelta(days=1))
    appointment_store.persist_cycle(conn, [appt], [], [], NOW)

    rescheduled = make_appt(start_time=NOW + timedelta(days=2))
    appointment_store.persist_cycle(conn, [rescheduled], [], [], NOW)

    prior = appointment_store.load_prior_state(conn)
    assert prior["1"].start_time == NOW + timedelta(days=2)


def test_silent_marks_recorded_without_duplicating_events(tmp_path):
    conn = appointment_store.connect(str(tmp_path / "state.db"))
    appt = make_appt()
    appointment_store.persist_cycle(conn, [appt], [], [("1", "reminder_60")], NOW)
    prior = appointment_store.load_prior_state(conn)
    assert "reminder_60" in prior["1"].fired_alerts


def test_prune_old_removes_past_appointments_and_alerts(tmp_path):
    conn = appointment_store.connect(str(tmp_path / "state.db"))
    old_appt = make_appt(appt_id="old", start_time=NOW - timedelta(days=3))
    recent_appt = make_appt(appt_id="recent", start_time=NOW + timedelta(days=1))
    event = AlertEvent(appointment_id="old", alert_type=CANCELLED, appointment=old_appt)
    appointment_store.persist_cycle(conn, [old_appt, recent_appt], [event], [], NOW)

    appointment_store.prune_old(conn, before=NOW - timedelta(days=1))

    prior = appointment_store.load_prior_state(conn)
    assert "old" not in prior
    assert "recent" in prior
    remaining_alerts = conn.execute("SELECT appointment_id FROM fired_alerts").fetchall()
    assert all(row["appointment_id"] != "old" for row in remaining_alerts)


def test_failure_counter_increments_and_resets(tmp_path):
    conn = appointment_store.connect(str(tmp_path / "state.db"))
    assert appointment_store.get_consecutive_failures(conn) == 0

    assert appointment_store.record_failure(conn) == 1
    assert appointment_store.record_failure(conn) == 2
    assert appointment_store.get_consecutive_failures(conn) == 2

    appointment_store.reset_failures(conn)
    assert appointment_store.get_consecutive_failures(conn) == 0


def test_failure_warning_sent_flag(tmp_path):
    conn = appointment_store.connect(str(tmp_path / "state.db"))
    assert appointment_store.get_failure_warning_sent(conn) is False

    appointment_store.mark_failure_warning_sent(conn)
    assert appointment_store.get_failure_warning_sent(conn) is True

    appointment_store.reset_failures(conn)
    assert appointment_store.get_failure_warning_sent(conn) is False
