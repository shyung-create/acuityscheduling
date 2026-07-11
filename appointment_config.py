"""Env-var-only configuration for the appointment alarm. No config.yaml, no
defaults baked in for credentials -- see .env.example. Fails fast and loudly
if a required variable is missing rather than silently no-op'ing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigError(Exception):
    """Raised when required environment variables are missing or invalid."""


@dataclass
class AppointmentMonitorConfig:
    acuity_user_id: str
    acuity_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    poll_window_days: int = 7
    reminder_thresholds_minutes: list[int] = field(default_factory=lambda: [1440, 60])
    failure_alert_threshold: int = 3
    state_db_path: str = "state.db"
    heartbeat_file_path: str = "heartbeat.txt"
    dry_run: bool = False


_REQUIRED_VARS = ("ACUITY_USER_ID", "ACUITY_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


def _parse_thresholds(raw: str) -> list[int]:
    thresholds = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            thresholds.append(int(part))
        except ValueError as exc:
            raise ConfigError(f"REMINDER_THRESHOLDS_MINUTES entry {part!r} is not an integer") from exc
    if not thresholds:
        raise ConfigError("REMINDER_THRESHOLDS_MINUTES must contain at least one integer")
    return thresholds


def load_config() -> AppointmentMonitorConfig:
    missing = [name for name in _REQUIRED_VARS if not os.environ.get(name)]
    if missing:
        raise ConfigError(f"missing required environment variable(s): {', '.join(missing)}")

    return AppointmentMonitorConfig(
        acuity_user_id=os.environ["ACUITY_USER_ID"],
        acuity_api_key=os.environ["ACUITY_API_KEY"],
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=os.environ["TELEGRAM_CHAT_ID"],
        poll_window_days=int(os.environ.get("POLL_WINDOW_DAYS") or "7"),
        reminder_thresholds_minutes=_parse_thresholds(os.environ.get("REMINDER_THRESHOLDS_MINUTES") or "1440,60"),
        failure_alert_threshold=int(os.environ.get("APPOINTMENT_FAILURE_ALERT_THRESHOLD") or "3"),
        state_db_path=os.environ.get("APPOINTMENT_STATE_DB_PATH") or "state.db",
        heartbeat_file_path=os.environ.get("HEARTBEAT_FILE_PATH") or "heartbeat.txt",
        dry_run=bool(os.environ.get("APPOINTMENT_DRY_RUN")),
    )
