from datetime import date

import notifications


def test_format_times_handles_colonless_utc_offset():
    # Regression test: Acuity's raw slot times look like
    # "2026-08-28T09:00:00-0700" -- no colon in the UTC offset.
    # datetime.fromisoformat() only accepts that form on Python 3.11+;
    # Ubuntu 22.04's default python3 is 3.10, where it raises ValueError.
    # This crashed live in production the first time a real slot was found.
    slots = [
        {"time": "2026-08-28T09:00:00-0700", "slotsAvailable": 1},
        {"time": "2026-08-28T14:30:00-0700", "slotsAvailable": 1},
    ]
    result = notifications._format_times(slots, "America/Los_Angeles")
    assert result == "09:00, 14:30"


def test_format_slot_alert_does_not_raise_on_colonless_offset():
    slots = [{"time": "2026-08-28T09:00:00-0700", "slotsAvailable": 1}]
    text, html = notifications.format_slot_alert(
        "E sharp hair", date(2026, 8, 28), slots, "America/Los_Angeles", "https://esharphair.as.me/schedule/x"
    )
    assert "09:00" in text
    assert "09:00" in html


def test_format_times_sorts_across_slots():
    slots = [
        {"time": "2026-08-28T14:30:00-0700", "slotsAvailable": 1},
        {"time": "2026-08-28T09:00:00-0700", "slotsAvailable": 1},
    ]
    assert notifications._format_times(slots, "America/Los_Angeles") == "09:00, 14:30"


def test_format_window_open_alert_contains_date_and_url():
    text, html = notifications.format_window_open_alert("E sharp hair", date(2026, 8, 28), "https://example.com/book")
    assert "2026-08-28" in text
    assert "https://example.com/book" in html


def test_format_heartbeat_unknown_when_no_furthest_date():
    text, _ = notifications.format_heartbeat(None)
    assert "unknown" in text


def test_format_heartbeat_includes_date():
    text, _ = notifications.format_heartbeat(date(2026, 9, 1))
    assert "2026-09-01" in text
