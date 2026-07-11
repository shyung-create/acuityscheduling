"""Date math and adaptive polling-interval logic.

Everything here is pure (no network, no I/O) so it can be unit tested
directly: the initial 2-month estimate, empirical window-flip detection,
furthest-bookable-date tracking, adaptive interval selection, and
time-window filtering of slot times.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from dateutil import parser as dateutil_parser
from dateutil.relativedelta import relativedelta

from config import PollConfig, TimeWindow


def initial_open_date_estimate(target_date: date, months_estimate: float = 2) -> date:
    """Rough initial guess of when a date's booking window opens.

    Uses dateutil.relativedelta so month-length edge cases (e.g. Aug 31 minus
    2 months, or Feb 29 on a leap year) are handled correctly instead of by
    hand. This is ONLY used to seed the initial polling cadence -- once the
    monitor observes a real false->true flip for a date, that observation
    replaces this estimate as ground truth (see refine_estimate_from_flip).
    """
    whole_months = int(months_estimate)
    remainder_days = round((months_estimate - whole_months) * 30)
    return target_date - relativedelta(months=whole_months) - timedelta(days=remainder_days)


def detect_flip(previous_value: Optional[bool], current_value: Optional[bool]) -> bool:
    """True iff a date went from not-bookable (or unknown) to bookable."""
    return current_value is True and previous_value is not True


def furthest_bookable_date(month_map: dict[str, bool]) -> Optional[date]:
    """Latest date in a single /availability/month response marked True."""
    true_dates = [date.fromisoformat(d) for d, available in month_map.items() if available]
    return max(true_dates) if true_dates else None


def combined_furthest_bookable_date(*dates: Optional[date]) -> Optional[date]:
    candidates = [d for d in dates if d is not None]
    return max(candidates) if candidates else None


def refine_estimate_from_flip(target_date: date, observed_open_date: date) -> float:
    """Given the real day a target date flipped open, express the shop's
    rolling booking-window length in (fractional) months, so future targets
    can be estimated from measured reality instead of the flat guess."""
    days = (target_date - observed_open_date).days
    return days / 30.44  # average month length; only used for display/logging


PHASE_FAR = "far"
PHASE_NEAR = "near"
PHASE_HOT = "hot"
PHASE_FOUND = "found"


def compute_poll_interval_seconds(
    now: datetime,
    estimate_open_date: date,
    target_date: date,
    slot_found: bool,
    poll: PollConfig,
    window_confirmed_open: bool = False,
    jitter_fraction: float = 0.1,
) -> tuple[int, str]:
    """Adaptive polling interval per the spec:

    - slot already found & alerted: poll.found_hours, until target_date passes
    - on estimate_open_date .. +24h ("hot window"): poll.hot_minutes (floor poll.min_minutes)
    - within 3 days before estimate_open_date ("near"): poll.near_minutes
    - otherwise ("far"): poll.far_hours
    - if the window has empirically opened (window_confirmed_open) but no
      slot is free yet and we're past the initial hot window, fall back to
      "near" cadence rather than all the way to "far" -- the window is known
      live, just currently full.

    Returns (seconds, phase_name). A small random jitter is applied so many
    instances (or GH Actions cron runs) don't hammer the site in lockstep.
    """
    today = now.date()

    if slot_found and today <= target_date:
        base_minutes = poll.found_hours * 60
        phase = PHASE_FOUND
    else:
        # "the estimated date and the following 24h" == the full calendar day
        # of estimate_open_date, plus another 24h after it -- 48h total from
        # midnight of estimate_open_date.
        hot_start = datetime.combine(estimate_open_date, datetime.min.time(), tzinfo=now.tzinfo)
        hot_end = hot_start + timedelta(hours=48)
        near_start = hot_start - timedelta(days=3)

        if hot_start <= now <= hot_end:
            base_minutes = poll.hot_minutes
            phase = PHASE_HOT
        elif near_start <= now < hot_start:
            base_minutes = poll.near_minutes
            phase = PHASE_NEAR
        elif window_confirmed_open and today <= target_date:
            base_minutes = poll.near_minutes
            phase = PHASE_NEAR
        else:
            base_minutes = poll.far_hours * 60
            phase = PHASE_FAR

    base_minutes = max(base_minutes, poll.min_minutes)
    jitter = base_minutes * jitter_fraction * (random.random() * 2 - 1)
    minutes = max(poll.min_minutes, base_minutes + jitter)
    return int(round(minutes * 60)), phase


def filter_slots_by_time_window(
    slots: Iterable[dict], window: TimeWindow, tz_name: str
) -> list[dict]:
    """Keep only slots whose local shop-time falls within [start, end]
    (inclusive, "HH:MM" 24h strings). No-op if the window isn't configured."""
    if not window.enabled:
        return list(slots)

    tz = ZoneInfo(tz_name)
    start_h, start_m = (int(x) for x in window.start.split(":"))
    end_h, end_m = (int(x) for x in window.end.split(":"))
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m

    kept = []
    for slot in slots:
        # Acuity's slot times ("...-0700", no colon in the offset) aren't
        # parseable by datetime.fromisoformat() on Python < 3.11 --
        # dateutil.parser.isoparse handles them on any version.
        dt = dateutil_parser.isoparse(slot["time"]).astimezone(tz)
        minutes = dt.hour * 60 + dt.minute
        if start_minutes <= minutes <= end_minutes:
            kept.append(slot)
    return kept
