"""SQLite persistence for the appointment alarm: one row per appointment
(latest known status/start time) plus a record of which alert types have
already fired for it, so a poll cycle never sends the same alert twice.
Also tracks consecutive Acuity-fetch failures so the "bot looks unhealthy"
warning fires exactly once per outage, not once per cycle.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Iterable

from appointment_client import Appointment
from appointment_diff import AlertEvent, PriorAppointment

SCHEMA = """
CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    start_time TEXT NOT NULL,
    client_name TEXT NOT NULL,
    service_name TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fired_alerts (
    appointment_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    fired_at TEXT NOT NULL,
    PRIMARY KEY (appointment_id, alert_type)
);

CREATE TABLE IF NOT EXISTS monitor_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_CONSECUTIVE_FAILURES_KEY = "consecutive_failures"
_FAILURE_WARNING_SENT_KEY = "failure_warning_sent"


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def is_empty(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM appointments LIMIT 1").fetchone()
    return row is None


def load_prior_state(conn: sqlite3.Connection) -> dict[str, PriorAppointment]:
    prior: dict[str, PriorAppointment] = {}
    for row in conn.execute("SELECT id, status, start_time FROM appointments"):
        prior[row["id"]] = PriorAppointment(
            status=row["status"],
            start_time=datetime.fromisoformat(row["start_time"]),
        )
    for row in conn.execute("SELECT appointment_id, alert_type FROM fired_alerts"):
        appt = prior.get(row["appointment_id"])
        if appt is not None:
            appt.fired_alerts.add(row["alert_type"])
    return prior


def persist_cycle(
    conn: sqlite3.Connection,
    current: Iterable[Appointment],
    events: Iterable[AlertEvent],
    silent_marks: Iterable[tuple[str, str]],
    now: datetime,
) -> None:
    now_iso = now.isoformat()
    with conn:
        for appt in current:
            conn.execute(
                """
                INSERT INTO appointments (id, status, start_time, client_name, service_name, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    start_time=excluded.start_time,
                    client_name=excluded.client_name,
                    service_name=excluded.service_name,
                    updated_at=excluded.updated_at
                """,
                (appt.id, appt.status, appt.start_time.isoformat(), appt.client_name, appt.service_name, now_iso),
            )
        for event in events:
            conn.execute(
                "INSERT OR IGNORE INTO fired_alerts (appointment_id, alert_type, fired_at) VALUES (?, ?, ?)",
                (event.appointment_id, event.alert_type, now_iso),
            )
        for appointment_id, alert_type in silent_marks:
            conn.execute(
                "INSERT OR IGNORE INTO fired_alerts (appointment_id, alert_type, fired_at) VALUES (?, ?, ?)",
                (appointment_id, alert_type, now_iso),
            )


def prune_old(conn: sqlite3.Connection, before: datetime) -> None:
    """Drops appointments (and their fired-alert records) whose start time
    is older than `before`, so the DB doesn't grow forever. Appointments
    naturally age out of the rolling poll window, so this just keeps a
    small trailing buffer rather than treating "aged out" as a cancellation."""
    before_iso = before.isoformat()
    with conn:
        stale_ids = [
            row["id"]
            for row in conn.execute("SELECT id FROM appointments WHERE start_time < ?", (before_iso,))
        ]
        if not stale_ids:
            return
        conn.executemany("DELETE FROM appointments WHERE id = ?", [(i,) for i in stale_ids])
        conn.executemany("DELETE FROM fired_alerts WHERE appointment_id = ?", [(i,) for i in stale_ids])


def get_consecutive_failures(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM monitor_meta WHERE key = ?", (_CONSECUTIVE_FAILURES_KEY,)).fetchone()
    return int(row["value"]) if row else 0


def get_failure_warning_sent(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM monitor_meta WHERE key = ?", (_FAILURE_WARNING_SENT_KEY,)).fetchone()
    return row is not None and row["value"] == "1"


def record_failure(conn: sqlite3.Connection) -> int:
    """Increments and returns the new consecutive-failure count."""
    count = get_consecutive_failures(conn) + 1
    _set_meta(conn, _CONSECUTIVE_FAILURES_KEY, str(count))
    return count


def mark_failure_warning_sent(conn: sqlite3.Connection) -> None:
    _set_meta(conn, _FAILURE_WARNING_SENT_KEY, "1")


def reset_failures(conn: sqlite3.Connection) -> None:
    """Called after a successful fetch: clears the failure streak and the
    warning-sent flag, so a future outage can warn again."""
    _set_meta(conn, _CONSECUTIVE_FAILURES_KEY, "0")
    _set_meta(conn, _FAILURE_WARNING_SENT_KEY, "0")


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO monitor_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
