"""Configuration loading for the haircut availability monitor.

Non-secret settings come from config.yaml; secrets come from environment
variables only (never committed to disk).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml


@dataclass
class TimeWindow:
    start: Optional[str] = None  # "HH:MM", shop-local time
    end: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.start and self.end)


@dataclass
class AcuityConfig:
    owner: str
    appointment_type_id: str
    calendar_id: str = "any"


@dataclass
class PollConfig:
    far_hours: float = 6
    near_minutes: float = 30
    hot_minutes: float = 5
    found_hours: float = 2
    min_minutes: float = 2  # hard floor, never poll faster than this


@dataclass
class ChannelsConfig:
    telegram: bool = True
    email: bool = True


@dataclass
class Config:
    target_dates: list[str]
    timezone: str = "America/Los_Angeles"
    time_window: TimeWindow = field(default_factory=TimeWindow)
    acuity: AcuityConfig = field(
        default_factory=lambda: AcuityConfig(owner="dc1e29cb", appointment_type_id="82222707")
    )
    booking_window_months_estimate: float = 2
    poll: PollConfig = field(default_factory=PollConfig)
    channels: ChannelsConfig = field(default_factory=ChannelsConfig)
    dry_run: bool = False
    alert_on_removal: bool = False
    heartbeat: bool = False
    state_file: str = "state.json"
    log_file: str = "monitor.log"
    booking_url: str = "https://esharphair.as.me/schedule/dc1e29cb/appointment/82222707"
    business_name: str = "E sharp hair"
    alert_rate_limit_seconds: int = 600  # 10 minutes, per spec


@dataclass
class Secrets:
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    email_to: Optional[str] = None


def load_config(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    tw = raw.get("time_window") or {}
    acuity_raw = raw.get("acuity") or {}
    poll_raw = raw.get("poll") or {}
    channels_raw = raw.get("channels") or {}

    # Empty/absent target_dates is valid -- e.g. the dashboard starts with no
    # dates and lets you add them at runtime, persisted into state.json rather
    # than back into this file.
    if "target_dates" in raw and not isinstance(raw["target_dates"], list):
        raise ValueError("config.yaml's target_dates must be a list of YYYY-MM-DD strings")

    return Config(
        target_dates=list(raw.get("target_dates") or []),
        timezone=raw.get("timezone", "America/Los_Angeles"),
        time_window=TimeWindow(start=tw.get("start"), end=tw.get("end")),
        acuity=AcuityConfig(
            owner=str(acuity_raw.get("owner", "dc1e29cb")),
            appointment_type_id=str(acuity_raw.get("appointment_type_id", "82222707")),
            calendar_id=str(acuity_raw.get("calendar_id", "any")),
        ),
        booking_window_months_estimate=float(raw.get("booking_window_months_estimate", 2)),
        poll=PollConfig(
            far_hours=float(poll_raw.get("far_hours", 6)),
            near_minutes=float(poll_raw.get("near_minutes", 30)),
            hot_minutes=float(poll_raw.get("hot_minutes", 5)),
            found_hours=float(poll_raw.get("found_hours", 2)),
            min_minutes=float(poll_raw.get("min_minutes", 2)),
        ),
        channels=ChannelsConfig(
            telegram=bool(channels_raw.get("telegram", True)),
            email=bool(channels_raw.get("email", True)),
        ),
        dry_run=bool(raw.get("dry_run", False)),
        alert_on_removal=bool(raw.get("alert_on_removal", False)),
        heartbeat=bool(raw.get("heartbeat", False)),
        state_file=raw.get("state_file", "state.json"),
        log_file=raw.get("log_file", "monitor.log"),
        booking_url=raw.get(
            "booking_url", "https://esharphair.as.me/schedule/dc1e29cb/appointment/82222707"
        ),
        business_name=raw.get("business_name", "E sharp hair"),
        alert_rate_limit_seconds=int(raw.get("alert_rate_limit_seconds", 600)),
    )


def load_secrets() -> Secrets:
    return Secrets(
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
        smtp_host=os.environ.get("SMTP_HOST"),
        # GitHub Actions' `env:` block sets SMTP_PORT="" (not unset) when the
        # repo secret isn't configured -- `.get(key, default)` only falls back
        # on a missing key, not an empty string, so `or` is required here.
        smtp_port=int(os.environ.get("SMTP_PORT") or "587"),
        smtp_user=os.environ.get("SMTP_USER"),
        smtp_pass=os.environ.get("SMTP_PASS"),
        email_to=os.environ.get("EMAIL_TO"),
    )
