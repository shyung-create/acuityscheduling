"""Orchestration for the appointment alarm: wires the Acuity client, SQLite
store, diff logic, and Telegram notifications into a single poll cycle.

Deliberately has no scheduler or sleep loop -- `run_cycle` runs exactly once
and returns. Timing is owned entirely by whatever invokes it (a systemd
timer in production; see systemd/acuity-alarm.timer).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import appointment_diff
import appointment_notifications
import appointment_store
import notifications
from appointment_client import AcuityAppointmentsClient, AcuityAppointmentsError, Appointment
from appointment_config import AppointmentMonitorConfig
from appointment_diff import AlertEvent

logger = logging.getLogger("appointment_alarm.monitor")

PRUNE_RETENTION = timedelta(days=1)


def fetch_current_appointments(
    client: AcuityAppointmentsClient, cfg: AppointmentMonitorConfig, now: datetime
) -> list[Appointment]:
    min_date = now.date()
    max_date = (now + timedelta(days=cfg.poll_window_days)).date()
    return client.get_appointments(min_date, max_date)


def run_cycle(
    client: AcuityAppointmentsClient,
    conn: sqlite3.Connection,
    cfg: AppointmentMonitorConfig,
    now: datetime | None = None,
) -> list[AlertEvent]:
    now = now or datetime.now(timezone.utc)

    try:
        current = fetch_current_appointments(client, cfg, now)
    except AcuityAppointmentsError as exc:
        logger.error("Acuity fetch failed, skipping this cycle: %s", exc)
        failures = appointment_store.record_failure(conn)
        if failures >= cfg.failure_alert_threshold and not appointment_store.get_failure_warning_sent(conn):
            text = appointment_notifications.format_unhealthy(failures, str(exc))
            notifications.send_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, text, dry_run=cfg.dry_run)
            appointment_store.mark_failure_warning_sent(conn)
            logger.warning("sent unhealthy-monitor warning after %d consecutive failures", failures)
        return []

    appointment_store.reset_failures(conn)

    prior = appointment_store.load_prior_state(conn)
    is_backfill = appointment_store.is_empty(conn)
    if is_backfill:
        logger.info("state DB is empty: backfilling %d appointment(s) without new_booking alerts", len(current))

    events, silent_marks = appointment_diff.diff_appointments(
        current, prior, now, cfg.reminder_thresholds_minutes, is_backfill
    )

    for event in events:
        text = appointment_notifications.message_for_event(event)
        ok = notifications.send_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, text, dry_run=cfg.dry_run)
        logger.info("alert %s for appointment %s: %s", event.alert_type, event.appointment_id, "sent" if ok else "FAILED")

    appointment_store.persist_cycle(conn, current, events, silent_marks, now)
    appointment_store.prune_old(conn, now - PRUNE_RETENTION)

    return events
