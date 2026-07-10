import json
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from dateutil.relativedelta import relativedelta

import scheduling
from config import PollConfig, TimeWindow

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name)) as fh:
        return json.load(fh)


# --- initial_open_date_estimate: month-length edge cases ---

def test_initial_estimate_simple_two_months():
    assert scheduling.initial_open_date_estimate(date(2026, 9, 26), 2) == date(2026, 7, 26)


def test_initial_estimate_handles_month_end_overflow():
    # Oct 31 minus 2 months -> Aug 31 is fine, but Oct 31 minus 1 month lands
    # on a shorter month (Sep has 30 days); relativedelta must clamp, not
    # overflow into October.
    assert scheduling.initial_open_date_estimate(date(2026, 10, 31), 1) == date(2026, 9, 30)


def test_initial_estimate_leap_year_feb_29():
    # 2028 is a leap year; Apr 29 minus 2 months -> Feb 29, 2028 (valid).
    assert scheduling.initial_open_date_estimate(date(2028, 4, 29), 2) == date(2028, 2, 29)


def test_initial_estimate_non_leap_year_clamps_feb():
    # Apr 30, 2026 minus 2 months would be Feb 30 -- invalid, must clamp to Feb 28 (2026 not leap).
    assert scheduling.initial_open_date_estimate(date(2026, 4, 30), 2) == date(2026, 2, 28)


def test_initial_estimate_fractional_months():
    # 1.5 months ~= 1 month + 15 days
    result = scheduling.initial_open_date_estimate(date(2026, 9, 30), 1.5)
    assert result == date(2026, 9, 30) - relativedelta(months=1) - timedelta(days=15)


# --- detect_flip: false->true boundary detection ---

def test_detect_flip_true_on_false_to_true():
    assert scheduling.detect_flip(False, True) is True


def test_detect_flip_false_when_already_true():
    assert scheduling.detect_flip(True, True) is False


def test_detect_flip_false_when_still_false():
    assert scheduling.detect_flip(False, False) is False


def test_detect_flip_true_when_previously_unknown():
    assert scheduling.detect_flip(None, True) is True


def test_detect_flip_false_on_true_to_false():
    assert scheduling.detect_flip(True, False) is False


# --- furthest_bookable_date ---

def test_furthest_bookable_date_from_fixture():
    month_map = load_fixture("month_sample.json")
    assert scheduling.furthest_bookable_date(month_map) == date(2026, 9, 27)


def test_furthest_bookable_date_none_when_all_false():
    assert scheduling.furthest_bookable_date({"2026-09-01": False, "2026-09-02": False}) is None


def test_combined_furthest_bookable_date_ignores_none():
    result = scheduling.combined_furthest_bookable_date(None, date(2026, 9, 27), date(2026, 9, 20))
    assert result == date(2026, 9, 27)


def test_combined_furthest_bookable_date_all_none():
    assert scheduling.combined_furthest_bookable_date(None, None) is None


# --- time-window filtering ---

def test_filter_slots_no_window_returns_all():
    slots = load_fixture("times_sample.json")["2026-09-26"]
    window = TimeWindow()
    assert scheduling.filter_slots_by_time_window(slots, window, "America/Los_Angeles") == slots


def test_filter_slots_keeps_only_within_window():
    slots = load_fixture("times_sample.json")["2026-09-26"]
    window = TimeWindow(start="09:00", end="18:00")
    filtered = scheduling.filter_slots_by_time_window(slots, window, "America/Los_Angeles")
    times = {s["time"] for s in filtered}
    assert times == {
        "2026-09-26T10:00:00-0700",
        "2026-09-26T10:45:00-0700",
        "2026-09-26T14:30:00-0700",
    }
    assert "2026-09-26T19:30:00-0700" not in times


def test_filter_slots_boundary_inclusive():
    slots = [{"time": "2026-09-26T09:00:00-0700", "slotsAvailable": 1}]
    window = TimeWindow(start="09:00", end="18:00")
    assert len(scheduling.filter_slots_by_time_window(slots, window, "America/Los_Angeles")) == 1


# --- adaptive interval selection ---

def _tz_now(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=ZoneInfo("America/Los_Angeles"))


def test_interval_far_phase_more_than_3_days_before_estimate():
    poll = PollConfig(far_hours=6, near_minutes=30, hot_minutes=5, found_hours=2, min_minutes=2)
    now = _tz_now(2026, 7, 1)
    estimate = date(2026, 7, 26)
    target = date(2026, 9, 26)
    seconds, phase = scheduling.compute_poll_interval_seconds(now, estimate, target, False, poll, jitter_fraction=0)
    assert phase == scheduling.PHASE_FAR
    assert seconds == 6 * 3600


def test_interval_near_phase_within_3_days_of_estimate():
    poll = PollConfig(far_hours=6, near_minutes=30, hot_minutes=5, found_hours=2, min_minutes=2)
    now = _tz_now(2026, 7, 24)
    estimate = date(2026, 7, 26)
    target = date(2026, 9, 26)
    seconds, phase = scheduling.compute_poll_interval_seconds(now, estimate, target, False, poll, jitter_fraction=0)
    assert phase == scheduling.PHASE_NEAR
    assert seconds == 30 * 60


def test_interval_hot_phase_on_estimate_date():
    poll = PollConfig(far_hours=6, near_minutes=30, hot_minutes=5, found_hours=2, min_minutes=2)
    now = _tz_now(2026, 7, 26, 10, 0)
    estimate = date(2026, 7, 26)
    target = date(2026, 9, 26)
    seconds, phase = scheduling.compute_poll_interval_seconds(now, estimate, target, False, poll, jitter_fraction=0)
    assert phase == scheduling.PHASE_HOT
    assert seconds == 5 * 60


def test_interval_hot_phase_within_24h_after_estimate():
    poll = PollConfig(far_hours=6, near_minutes=30, hot_minutes=5, found_hours=2, min_minutes=2)
    now = _tz_now(2026, 7, 27, 5, 0)  # 5h into the following day
    estimate = date(2026, 7, 26)
    target = date(2026, 9, 26)
    seconds, phase = scheduling.compute_poll_interval_seconds(now, estimate, target, False, poll, jitter_fraction=0)
    assert phase == scheduling.PHASE_HOT


def test_interval_found_phase_after_slot_found():
    poll = PollConfig(far_hours=6, near_minutes=30, hot_minutes=5, found_hours=2, min_minutes=2)
    now = _tz_now(2026, 8, 1)
    estimate = date(2026, 7, 26)
    target = date(2026, 9, 26)
    seconds, phase = scheduling.compute_poll_interval_seconds(now, estimate, target, True, poll, jitter_fraction=0)
    assert phase == scheduling.PHASE_FOUND
    assert seconds == 2 * 3600


def test_interval_found_phase_stops_after_target_date_passes():
    poll = PollConfig(far_hours=6, near_minutes=30, hot_minutes=5, found_hours=2, min_minutes=2)
    now = _tz_now(2026, 9, 27)  # day after target
    estimate = date(2026, 7, 26)
    target = date(2026, 9, 26)
    seconds, phase = scheduling.compute_poll_interval_seconds(now, estimate, target, True, poll, jitter_fraction=0)
    assert phase != scheduling.PHASE_FOUND


def test_interval_window_confirmed_open_falls_back_to_near_not_far():
    poll = PollConfig(far_hours=6, near_minutes=30, hot_minutes=5, found_hours=2, min_minutes=2)
    now = _tz_now(2026, 8, 15)  # well past the 24h hot window, before target
    estimate = date(2026, 7, 26)
    target = date(2026, 9, 26)
    seconds, phase = scheduling.compute_poll_interval_seconds(
        now, estimate, target, False, poll, window_confirmed_open=True, jitter_fraction=0
    )
    assert phase == scheduling.PHASE_NEAR


def test_interval_never_below_floor():
    poll = PollConfig(far_hours=6, near_minutes=30, hot_minutes=1, found_hours=2, min_minutes=2)
    now = _tz_now(2026, 7, 26, 10, 0)
    estimate = date(2026, 7, 26)
    target = date(2026, 9, 26)
    seconds, phase = scheduling.compute_poll_interval_seconds(now, estimate, target, False, poll, jitter_fraction=0)
    assert seconds >= poll.min_minutes * 60
