"""Small JSON state file: last-seen slots per target date, alert dedup /
rate-limiting, furthest-bookable-date tracking, and consecutive-failure
counters. Written atomically so a crash mid-write can't corrupt it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

STATE_VERSION = 1


def _default_target_state() -> dict:
    return {
        "seen_slot_times": [],
        "window_open_alerted": False,
        "last_alert_ts": {},  # channel -> unix timestamp
        "consecutive_failures": 0,
        "failure_warning_sent": False,
        "estimated_open_date": None,
        "measured_open_date": None,
        "last_month_map": {},
    }


def default_state() -> dict:
    return {
        "version": STATE_VERSION,
        "targets": {},
        "last_heartbeat_ts": None,
    }


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return default_state()
    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError:
            return default_state()
    data.setdefault("version", STATE_VERSION)
    data.setdefault("targets", {})
    data.setdefault("last_heartbeat_ts", None)
    return data


def save_state(path: str, state: dict) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def get_target_state(state: dict, date_str: str) -> dict:
    return state["targets"].setdefault(date_str, _default_target_state())


def diff_slot_times(previous_times: set[str], current_times: set[str]) -> tuple[set[str], set[str]]:
    """Returns (new_times, removed_times)."""
    return current_times - previous_times, previous_times - current_times


def can_alert(target_state: dict, channel: str, now: datetime, rate_limit_seconds: int) -> bool:
    last_ts = target_state["last_alert_ts"].get(channel)
    if last_ts is None:
        return True
    return (now.timestamp() - last_ts) >= rate_limit_seconds


def record_alert(target_state: dict, channel: str, now: datetime) -> None:
    target_state["last_alert_ts"][channel] = now.timestamp()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
