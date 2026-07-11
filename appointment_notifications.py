"""Telegram message templates for the appointment alarm. These are a
personal alert to yourself, not client-facing copy, so they're kept to
client name, service, and time -- nothing else. Delivery itself reuses
notifications.send_telegram (raw Bot API call), not a separate client.
"""
from __future__ import annotations

from appointment_client import Appointment
from appointment_diff import CANCELLED, NEW_BOOKING, RESCHEDULED, AlertEvent


def _fmt(dt) -> str:
    return dt.strftime("%a %m/%d %I:%M%p").replace(" 0", " ")


def _threshold_label(minutes: int) -> str:
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def format_new_booking(appt: Appointment) -> str:
    return f"\U0001F195 New booking\n{appt.client_name} — {appt.service_name}\n{_fmt(appt.start_time)}"


def format_cancelled(appt: Appointment) -> str:
    return f"❌ Cancelled\n{appt.client_name} — {appt.service_name}\n{_fmt(appt.start_time)}"


def format_rescheduled(appt: Appointment, previous_start_time) -> str:
    return (
        f"\U0001F504 Rescheduled\n{appt.client_name} — {appt.service_name}\n"
        f"{_fmt(previous_start_time)} → {_fmt(appt.start_time)}"
    )


def format_reminder(appt: Appointment, threshold_minutes: int) -> str:
    label = _threshold_label(threshold_minutes)
    return f"⏰ {label} reminder\n{appt.client_name} — {appt.service_name}\n{_fmt(appt.start_time)}"


def format_unhealthy(consecutive_failures: int, reason: str) -> str:
    return (
        f"\U0001F6A8 Appointment monitor unhealthy\n"
        f"{consecutive_failures} consecutive poll failures.\nLast error: {reason}"
    )


def message_for_event(event: AlertEvent) -> str:
    if event.alert_type == NEW_BOOKING:
        return format_new_booking(event.appointment)
    if event.alert_type == CANCELLED:
        return format_cancelled(event.appointment)
    if event.alert_type == RESCHEDULED:
        return format_rescheduled(event.appointment, event.previous_start_time)
    if event.alert_type.startswith("reminder_"):
        threshold_minutes = int(event.alert_type.removeprefix("reminder_"))
        return format_reminder(event.appointment, threshold_minutes)
    raise ValueError(f"no message template for alert_type={event.alert_type!r}")
