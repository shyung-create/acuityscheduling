"""Pure diff logic for the appointment alarm: given the appointments Acuity
currently reports and what we last knew about each one, decide which alerts
(if any) should fire this cycle. No network, no SQLite -- everything here is
a plain function so it can be unit tested directly against fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from appointment_client import STATUS_CANCELED, Appointment

NEW_BOOKING = "new_booking"
CANCELLED = "cancelled"
RESCHEDULED = "rescheduled"


def reminder_alert_type(threshold_minutes: int) -> str:
    return f"reminder_{threshold_minutes}"


@dataclass
class PriorAppointment:
    """What state_store last persisted for an appointment ID."""

    status: str
    start_time: datetime
    fired_alerts: set[str] = field(default_factory=set)


@dataclass
class AlertEvent:
    appointment_id: str
    alert_type: str
    appointment: Appointment
    previous_start_time: Optional[datetime] = None  # set only for RESCHEDULED


def diff_appointments(
    current: list[Appointment],
    prior: dict[str, PriorAppointment],
    now: datetime,
    reminder_thresholds_minutes: list[int],
    is_backfill: bool,
) -> tuple[list[AlertEvent], list[tuple[str, str]]]:
    """Compares `current` (this cycle's fetch) against `prior` (last known
    state per appointment ID) and returns:

      events        -- alerts that should actually be sent to Telegram
      silent_marks   -- (appointment_id, alert_type) pairs to record as
                        already-fired WITHOUT sending anything

    `is_backfill` is True only on the very first-ever cycle (empty state DB)
    -- it suppresses new_booking for appointments that were already on the
    calendar before the alarm existed, so turning this on doesn't flood you
    with "new booking" pings for everything already booked.

    For any appointment seen for the first time (new_booking or backfill),
    reminder thresholds that have already elapsed by the time we first see
    it are silently marked fired rather than sent: the new_booking alert (or
    the mere fact that it's a pre-existing appointment you already know
    about) already told you it exists, so a redundant "reminder" moments
    later would just be noise. Thresholds still in the future fire normally
    later.
    """
    events: list[AlertEvent] = []
    silent_marks: list[tuple[str, str]] = []

    for appt in current:
        prev = prior.get(appt.id)

        if prev is None:
            if not is_backfill:
                events.append(AlertEvent(appt.id, NEW_BOOKING, appt))
            if appt.status != STATUS_CANCELED:
                minutes_until = _minutes_until(appt.start_time, now)
                for threshold in reminder_thresholds_minutes:
                    if minutes_until <= threshold:
                        silent_marks.append((appt.id, reminder_alert_type(threshold)))
            continue

        if appt.status == STATUS_CANCELED:
            if prev.status != STATUS_CANCELED and CANCELLED not in prev.fired_alerts:
                events.append(AlertEvent(appt.id, CANCELLED, appt))
            continue

        if prev.status == STATUS_CANCELED:
            # Acuity appointments don't come back from canceled; ignore
            # defensively rather than treating this as a new booking.
            continue

        if appt.start_time != prev.start_time:
            events.append(AlertEvent(appt.id, RESCHEDULED, appt, previous_start_time=prev.start_time))

        minutes_until = _minutes_until(appt.start_time, now)
        for threshold in sorted(reminder_thresholds_minutes):
            alert_type = reminder_alert_type(threshold)
            if 0 < minutes_until <= threshold and alert_type not in prev.fired_alerts:
                events.append(AlertEvent(appt.id, alert_type, appt))

    return events, silent_marks


def _minutes_until(start_time: datetime, now: datetime) -> float:
    return (start_time - now).total_seconds() / 60
