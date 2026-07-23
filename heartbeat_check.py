#!/usr/bin/env python3
"""Dead-man's-switch for the haircut availability monitor (monitor.py /
dashboard.py). Standalone on purpose: it imports nothing from this repo
and nothing outside the Python standard library, so a bug in the Acuity
client, the Telegram formatting code, or a broken venv can't also take out
the alert that says the poller itself looks stuck.

Reads the heartbeat file monitor.py/dashboard.py write after each poll
cycle -- {"last_poll": ..., "next_expected_by": ...}. next_expected_by
already accounts for whichever adaptive-polling phase was active when it
was written (cycles can legitimately be hours apart in the "far" phase),
so this doesn't need its own flat staleness threshold -- it just compares
against that timestamp directly.

If it's missing, unparseable, or now is past next_expected_by, sends one
Telegram message via a raw HTTP POST (stdlib only) and records that it did
so in a sentinel file next to the heartbeat file, so a prolonged outage
sends exactly one alert (not one per 30-minute check) -- the sentinel is
cleared as soon as the heartbeat is fresh again, so a future outage alerts
again.

Run by heartbeat-check.timer, independently of haircut-dashboard.service.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

HEARTBEAT_FILE_PATH = os.environ.get("HEARTBEAT_FILE_PATH", "/opt/acuity-alarm/heartbeat.txt")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

ALERT_SENTINEL_PATH = HEARTBEAT_FILE_PATH + ".alerted"


def read_heartbeat(path: str) -> tuple[datetime | None, datetime | None]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return datetime.fromisoformat(data["last_poll"]), datetime.fromisoformat(data["next_expected_by"])
    except (FileNotFoundError, ValueError, KeyError, OSError, json.JSONDecodeError):
        return None, None


def send_telegram(token: str, chat_id: str, text: str, timeout: float = 10) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError) as exc:
        print(f"heartbeat-check: failed to send Telegram alert: {exc}", file=sys.stderr)
        return False


def main() -> int:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("heartbeat-check: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set, cannot alert", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    last_poll, next_expected_by = read_heartbeat(HEARTBEAT_FILE_PATH)
    already_alerted = os.path.exists(ALERT_SENTINEL_PATH)

    is_stale = next_expected_by is None or now > next_expected_by

    if not is_stale:
        age_minutes = (now - last_poll).total_seconds() / 60
        print(f"heartbeat-check: OK (last poll {age_minutes:.1f} min ago, next expected by {next_expected_by.isoformat()})")
        if already_alerted:
            os.remove(ALERT_SENTINEL_PATH)
            print("heartbeat-check: heartbeat recovered, cleared alert sentinel")
        return 0

    if already_alerted:
        print("heartbeat-check: still stale, already alerted -- not re-sending")
        return 0

    if last_poll is None:
        text = (
            "\U0001F6A8 Haircut monitor heartbeat missing\n"
            f"No heartbeat file at {HEARTBEAT_FILE_PATH} -- the poller may never have run, "
            "or haircut-dashboard.service is not active.\n"
            "Check: systemctl status haircut-dashboard.service"
        )
    else:
        overdue_minutes = (now - next_expected_by).total_seconds() / 60
        text = (
            "\U0001F6A8 Haircut monitor looks stuck\n"
            f"Last poll was at {last_poll.isoformat()}, expected another by "
            f"{next_expected_by.isoformat()} ({overdue_minutes:.0f} min overdue).\n"
            "Check: systemctl status haircut-dashboard.service, journalctl -u haircut-dashboard.service"
        )

    sent = send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, text)
    if sent:
        with open(ALERT_SENTINEL_PATH, "w", encoding="utf-8") as fh:
            fh.write(now.isoformat())
    print(f"heartbeat-check: stale, alert {'sent' if sent else 'FAILED'}")
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
