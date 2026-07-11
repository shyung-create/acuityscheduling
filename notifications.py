"""Telegram Bot API and SMTP email notification channels, plus the message
formatting shared between them.
"""
from __future__ import annotations

import logging
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from dateutil import parser as dateutil_parser

logger = logging.getLogger("haircut_alarm.notifications")

TELEGRAM_API_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"


def _format_times(slots: list[dict], tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    # Acuity's slot times look like "2026-08-28T09:00:00-0700" -- no colon in
    # the UTC offset. datetime.fromisoformat() only accepts that form on
    # Python 3.11+; Ubuntu 22.04's default python3 is 3.10, where it raises
    # ValueError. dateutil.parser.isoparse handles it on any version.
    times = sorted(dateutil_parser.isoparse(s["time"]).astimezone(tz) for s in slots)
    return ", ".join(t.strftime("%H:%M") for t in times)


def format_slot_alert(
    business_name: str, target_date: date, slots: list[dict], tz_name: str, booking_url: str
) -> tuple[str, str]:
    """Returns (plain_text, html) for a "slot available" alert."""
    times_str = _format_times(slots, tz_name)
    date_str = target_date.strftime("%a %Y-%m-%d")
    text = (
        f"\U0001F389 Haircut slot open — {business_name}\n"
        f"Date: {date_str}\n"
        f"Times: {times_str} ({tz_name})\n"
        f"Book NOW: {booking_url}"
    )
    html = (
        f"<p>\U0001F389 <b>Haircut slot open — {business_name}</b></p>"
        f"<p>Date: {date_str}<br>Times: {times_str} ({tz_name})</p>"
        f'<p><a href="{booking_url}" style="font-size:1.2em;font-weight:bold;">Book now</a></p>'
    )
    return text, html


def format_window_open_alert(business_name: str, target_date: date, booking_url: str) -> tuple[str, str]:
    date_str = target_date.strftime("%a %Y-%m-%d")
    text = (
        f"\U0001F513 Booking window reached your date — {business_name}\n"
        f"{date_str} is now within the bookable window (no slot free yet, or it's fully booked already).\n"
        f"{booking_url}"
    )
    html = f"<p>\U0001F513 Booking window reached {date_str} for {business_name}. <a href='{booking_url}'>Check now</a></p>"
    return text, html


def format_removal_alert(business_name: str, target_date: date, tz_name: str, booking_url: str) -> tuple[str, str]:
    date_str = target_date.strftime("%a %Y-%m-%d")
    text = (
        f"⚠️ Previously-seen slot(s) disappeared — {business_name}\n"
        f"Date: {date_str} ({tz_name})\n{booking_url}"
    )
    html = f"<p>⚠️ Previously-seen slot(s) disappeared for {date_str}, {business_name}.</p>"
    return text, html


def format_warning_alert(reason: str) -> tuple[str, str]:
    text = f"⚠️ Haircut monitor WARNING\n{reason}"
    html = f"<p>⚠️ Haircut monitor WARNING</p><p>{reason}</p>"
    return text, html


def format_heartbeat(furthest_bookable_date: Optional[date]) -> tuple[str, str]:
    fb = furthest_bookable_date.isoformat() if furthest_bookable_date else "unknown"
    text = f"\U0001FA79 Still watching. Furthest bookable date currently: {fb}"
    html = f"<p>\U0001FA79 Still watching. Furthest bookable date currently: {fb}</p>"
    return text, html


def send_telegram(token: str, chat_id: str, text: str, dry_run: bool = False, timeout: float = 10) -> bool:
    if dry_run:
        logger.info("[dry-run] would send Telegram message: %s", text)
        return True
    url = TELEGRAM_API_TEMPLATE.format(token=token)
    try:
        resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=timeout)
        if resp.status_code != 200:
            logger.error("Telegram send failed: HTTP %s %s", resp.status_code, resp.text[:300])
            return False
        return True
    except requests.RequestException as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


def send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    to_addr: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
    dry_run: bool = False,
    timeout: float = 15,
) -> bool:
    if dry_run:
        logger.info("[dry-run] would send email %r to %s: %s", subject, to_addr, text_body)
        return True

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg.attach(MIMEText(text_body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=timeout) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [to_addr], msg.as_string())
        return True
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("Email send failed: %s", exc)
        return False
