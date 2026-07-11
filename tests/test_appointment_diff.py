from datetime import datetime, timedelta, timezone

from appointment_client import STATUS_ACTIVE, STATUS_CANCELED, Appointment
from appointment_diff import (
    CANCELLED,
    NEW_BOOKING,
    RESCHEDULED,
    PriorAppointment,
    diff_appointments,
    reminder_alert_type,
)

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def make_appt(appt_id="1", status=STATUS_ACTIVE, start_time=None, name="Jane Doe", service="Haircut"):
    return Appointment(
        id=appt_id,
        status=status,
        start_time=start_time or (NOW + timedelta(days=1)),
        client_name=name,
        service_name=service,
    )


def test_new_booking_fires_when_not_backfill():
    appt = make_appt(start_time=NOW + timedelta(days=3))
    events, marks = diff_appointments([appt], prior={}, now=NOW, reminder_thresholds_minutes=[1440, 60], is_backfill=False)
    assert [e.alert_type for e in events] == [NEW_BOOKING]
    assert events[0].appointment_id == "1"
    assert marks == []


def test_new_booking_suppressed_during_backfill():
    appt = make_appt(start_time=NOW + timedelta(days=3))
    events, marks = diff_appointments([appt], prior={}, now=NOW, reminder_thresholds_minutes=[1440, 60], is_backfill=True)
    assert events == []


def test_new_appointment_already_within_threshold_is_silently_marked_not_alerted():
    # Booked 30 minutes before it starts -- new_booking already told us about
    # it, so the 60-minute reminder shouldn't also fire right away.
    appt = make_appt(start_time=NOW + timedelta(minutes=30))
    events, marks = diff_appointments([appt], prior={}, now=NOW, reminder_thresholds_minutes=[1440, 60], is_backfill=False)
    assert [e.alert_type for e in events] == [NEW_BOOKING]
    assert (appt.id, reminder_alert_type(60)) in marks
    assert (appt.id, reminder_alert_type(1440)) in marks  # 30min is also <= 1440


def test_new_appointment_far_out_does_not_mark_reminders():
    appt = make_appt(start_time=NOW + timedelta(days=10))
    events, marks = diff_appointments([appt], prior={}, now=NOW, reminder_thresholds_minutes=[1440, 60], is_backfill=False)
    assert marks == []


def test_no_events_when_nothing_changed():
    start = NOW + timedelta(days=5)
    appt = make_appt(start_time=start)
    prior = {"1": PriorAppointment(status=STATUS_ACTIVE, start_time=start)}
    events, marks = diff_appointments([appt], prior=prior, now=NOW, reminder_thresholds_minutes=[1440, 60], is_backfill=False)
    assert events == []
    assert marks == []


def test_cancelled_fires_once():
    start = NOW + timedelta(days=2)
    appt = make_appt(status=STATUS_CANCELED, start_time=start)
    prior = {"1": PriorAppointment(status=STATUS_ACTIVE, start_time=start)}
    events, _ = diff_appointments([appt], prior=prior, now=NOW, reminder_thresholds_minutes=[60], is_backfill=False)
    assert [e.alert_type for e in events] == [CANCELLED]


def test_cancelled_not_fired_twice_across_cycles():
    start = NOW + timedelta(days=2)
    appt = make_appt(status=STATUS_CANCELED, start_time=start)
    # Already canceled on a previous cycle, and the CANCELLED alert already fired.
    prior = {"1": PriorAppointment(status=STATUS_CANCELED, start_time=start, fired_alerts={CANCELLED})}
    events, _ = diff_appointments([appt], prior=prior, now=NOW, reminder_thresholds_minutes=[60], is_backfill=False)
    assert events == []


def test_cancelled_appointment_gets_no_reminder_or_reschedule():
    old_start = NOW + timedelta(hours=2)
    new_start = NOW + timedelta(minutes=30)
    appt = make_appt(status=STATUS_CANCELED, start_time=new_start)
    prior = {"1": PriorAppointment(status=STATUS_ACTIVE, start_time=old_start)}
    events, _ = diff_appointments([appt], prior=prior, now=NOW, reminder_thresholds_minutes=[60], is_backfill=False)
    assert [e.alert_type for e in events] == [CANCELLED]
    assert RESCHEDULED not in [e.alert_type for e in events]


def test_rescheduled_fires_on_start_time_change():
    old_start = NOW + timedelta(days=5)
    new_start = NOW + timedelta(days=6)
    appt = make_appt(start_time=new_start)
    prior = {"1": PriorAppointment(status=STATUS_ACTIVE, start_time=old_start)}
    events, _ = diff_appointments([appt], prior=prior, now=NOW, reminder_thresholds_minutes=[1440, 60], is_backfill=False)
    resched = [e for e in events if e.alert_type == RESCHEDULED]
    assert len(resched) == 1
    assert resched[0].previous_start_time == old_start
    assert resched[0].appointment.start_time == new_start


def test_reschedule_and_reminder_can_both_fire_same_cycle():
    old_start = NOW + timedelta(days=5)
    new_start = NOW + timedelta(minutes=45)  # rescheduled to very soon
    appt = make_appt(start_time=new_start)
    prior = {"1": PriorAppointment(status=STATUS_ACTIVE, start_time=old_start)}
    events, _ = diff_appointments([appt], prior=prior, now=NOW, reminder_thresholds_minutes=[60], is_backfill=False)
    types = {e.alert_type for e in events}
    assert types == {RESCHEDULED, reminder_alert_type(60)}


def test_reminder_fires_when_crossing_threshold():
    start = NOW + timedelta(minutes=50)
    appt = make_appt(start_time=start)
    prior = {"1": PriorAppointment(status=STATUS_ACTIVE, start_time=start)}
    events, _ = diff_appointments([appt], prior=prior, now=NOW, reminder_thresholds_minutes=[60], is_backfill=False)
    assert [e.alert_type for e in events] == [reminder_alert_type(60)]


def test_reminder_not_fired_twice():
    start = NOW + timedelta(minutes=50)
    appt = make_appt(start_time=start)
    prior = {"1": PriorAppointment(status=STATUS_ACTIVE, start_time=start, fired_alerts={reminder_alert_type(60)})}
    events, _ = diff_appointments([appt], prior=prior, now=NOW, reminder_thresholds_minutes=[60], is_backfill=False)
    assert events == []


def test_reminder_thresholds_are_independent():
    # Within both the 24h and the 1h windows at once (first poll after booking
    # crossed both) -- both should fire, each exactly once.
    start = NOW + timedelta(minutes=50)
    appt = make_appt(start_time=start)
    prior = {"1": PriorAppointment(status=STATUS_ACTIVE, start_time=start)}
    events, _ = diff_appointments([appt], prior=prior, now=NOW, reminder_thresholds_minutes=[1440, 60], is_backfill=False)
    types = {e.alert_type for e in events}
    assert types == {reminder_alert_type(1440), reminder_alert_type(60)}


def test_reminder_does_not_fire_for_past_appointment():
    start = NOW - timedelta(minutes=5)  # already started
    appt = make_appt(start_time=start)
    prior = {"1": PriorAppointment(status=STATUS_ACTIVE, start_time=start)}
    events, _ = diff_appointments([appt], prior=prior, now=NOW, reminder_thresholds_minutes=[1440, 60], is_backfill=False)
    assert events == []


def test_canceled_appointment_never_reactivates_as_new():
    start = NOW + timedelta(days=1)
    appt = make_appt(status=STATUS_ACTIVE, start_time=start)
    prior = {"1": PriorAppointment(status=STATUS_CANCELED, start_time=start, fired_alerts={CANCELLED})}
    events, marks = diff_appointments([appt], prior=prior, now=NOW, reminder_thresholds_minutes=[60], is_backfill=False)
    assert events == []
    assert marks == []


def test_multiple_appointments_independent_events():
    new_appt = make_appt(appt_id="new", start_time=NOW + timedelta(days=4))
    cancelled_appt = make_appt(appt_id="c", status=STATUS_CANCELED, start_time=NOW + timedelta(days=1))
    unchanged_appt = make_appt(appt_id="u", start_time=NOW + timedelta(days=2))
    prior = {
        "c": PriorAppointment(status=STATUS_ACTIVE, start_time=NOW + timedelta(days=1)),
        "u": PriorAppointment(status=STATUS_ACTIVE, start_time=NOW + timedelta(days=2)),
    }
    events, _ = diff_appointments(
        [new_appt, cancelled_appt, unchanged_appt],
        prior=prior, now=NOW, reminder_thresholds_minutes=[60], is_backfill=False,
    )
    fired = {(e.appointment_id, e.alert_type) for e in events}
    assert fired == {("new", NEW_BOOKING), ("c", CANCELLED)}
