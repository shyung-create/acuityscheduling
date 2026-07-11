#!/usr/bin/env python3
"""Dead-man's-switch for the appointment alarm. Standalone on purpose: it
imports nothing from this repo (no appointment_client.py, no
appointment_config.py, no notifications.py) and nothing outside the Python
standard library, so a broken Acuity integration, a broken Telegram
formatting change, or a broken venv can't also take out the alert that says
the poller itself looks stuck.

Reads the heartbeat file main.py writes after each successful poll cycle
(see main.py's write_heartbeat). If it's missing, or older than
HEARTBEAT_STALE_MINUTES, sends one Telegram message via a raw HTTP POST and
records that it did so in a sentinel file next to the heartbeat file, so a
prolonged outage sends exactly one alert (not one per 30-minute check) --
the sentinel is cleared as soon as the heartbeat is fresh again, so a future
outage alerts again.

Run by heartbeat-check.timer, independently of acuity-alarm.timer.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

HEARTBEAT_FILE_PATH = os.environ.get("HEARTBEAT_FILE_PATH", "/opt/acuity-alarm/heartbeat.txt")
STALE_MINUTES = float(os.environ.get("HEARTBEAT_STALE_MINUTES", "20"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

ALERT_SENTINEL_PATH = HEARTBEAT_FILE_PATH + ".alerted"


def read_heartbeat(path: str) -> datetime | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return datetime.fromisoformat(fh.read().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


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
    last_beat = read_heartbeat(HEARTBEAT_FILE_PATH)
    already_alerted = os.path.exists(ALERT_SENTINEL_PATH)

    is_stale = last_beat is None or (now - last_beat).total_seconds() / 60 > STALE_MINUTES

    if not is_stale:
        print(f"heartbeat-check: OK ({(now - last_beat).total_seconds() / 60:.1f} min old)")
        if already_alerted:
            os.remove(ALERT_SENTINEL_PATH)
            print("heartbeat-check: heartbeat recovered, cleared alert sentinel")
        return 0

    if already_alerted:
        print("heartbeat-check: still stale, already alerted -- not re-sending")
        return 0

    if last_beat is None:
        text = (
            "\U0001F6A8 Appointment alarm heartbeat missing\n"
            f"No heartbeat file at {HEARTBEAT_FILE_PATH} -- the poller may never have run, "
            "or acuity-alarm.timer is not active.\n"
            "Check: systemctl status acuity-alarm.timer"
        )
    else:
        age_minutes = (now - last_beat).total_seconds() / 60
        text = (
            "\U0001F6A8 Appointment alarm looks stuck\n"
            f"Last successful poll cycle was {age_minutes:.0f} min ago "
            f"(expected within {STALE_MINUTES:.0f} min).\n"
            "Check: systemctl status acuity-alarm.timer, journalctl -u acuity-alarm.service"
        )

    sent = send_telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, text)
    if sent:
        with open(ALERT_SENTINEL_PATH, "w", encoding="utf-8") as fh:
            fh.write(now.isoformat())
    print(f"heartbeat-check: stale heartbeat, alert {'sent' if sent else 'FAILED'}")
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
