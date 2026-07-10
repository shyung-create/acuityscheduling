#!/usr/bin/env python3
"""Acuity appointment-availability alarm for E sharp hair.

Watches https://esharphair.as.me/schedule/dc1e29cb/appointment/82222707 for
a bookable slot on configured target date(s) and alerts via Telegram and/or
email the moment one appears. See README.md for setup.

Usage:
    python monitor.py --once                 # single check, exit (cron/GH Actions)
    python monitor.py                        # long-running daemon, adaptive interval
    python monitor.py --once --dry-run        # log instead of sending notifications
    python monitor.py --engine=browser        # Playwright fallback if requests gets blocked
    python monitor.py --dismiss 2026-09-26     # stop alerting for a date you've already booked
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import signal
import sys
import time
from datetime import date, datetime, timezone
from typing import Optional

import config as config_mod
import notifications
import scheduling
import state_store
from acuity_client import AcuityClient, AcuityError

logger = logging.getLogger("haircut_alarm")

_shutdown_requested = False


def _handle_shutdown_signal(signum, frame):  # noqa: ARG001
    global _shutdown_requested
    _shutdown_requested = True


def setup_logging(log_file: str, verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger("haircut_alarm")
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    root.addHandler(stream_handler)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=2_000_000, backupCount=3
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning("could not open log file %s: %s (stdout logging only)", log_file, exc)


def build_client(engine: str, cfg: config_mod.Config):
    if engine == "browser":
        from browser_engine import BrowserAcuityClient

        return BrowserAcuityClient(
            owner=cfg.acuity.owner,
            appointment_type_id=cfg.acuity.appointment_type_id,
            calendar_id=cfg.acuity.calendar_id,
            timezone=cfg.timezone,
        )
    return AcuityClient(
        owner=cfg.acuity.owner,
        appointment_type_id=cfg.acuity.appointment_type_id,
        calendar_id=cfg.acuity.calendar_id,
        timezone=cfg.timezone,
    )


def send_alert(
    cfg: config_mod.Config,
    secrets: config_mod.Secrets,
    tstate: dict,
    now: datetime,
    text: str,
    html: str,
    subject: str,
) -> None:
    if cfg.channels.telegram:
        if not state_store.can_alert(tstate, "telegram", now, cfg.alert_rate_limit_seconds):
            logger.info("telegram alert suppressed by rate limit")
        elif not (secrets.telegram_bot_token and secrets.telegram_chat_id):
            logger.warning("telegram channel enabled but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set")
        else:
            ok = notifications.send_telegram(
                secrets.telegram_bot_token, secrets.telegram_chat_id, text, dry_run=cfg.dry_run
            )
            state_store.record_alert(tstate, "telegram", now)
            logger.info("telegram alert %s", "sent" if ok else "FAILED")

    if cfg.channels.email:
        if not state_store.can_alert(tstate, "email", now, cfg.alert_rate_limit_seconds):
            logger.info("email alert suppressed by rate limit")
        elif not (secrets.smtp_host and secrets.smtp_user and secrets.smtp_pass and secrets.email_to):
            logger.warning("email channel enabled but SMTP_* / EMAIL_TO not fully set")
        else:
            ok = notifications.send_email(
                secrets.smtp_host,
                secrets.smtp_port,
                secrets.smtp_user,
                secrets.smtp_pass,
                secrets.email_to,
                subject,
                text,
                html,
                dry_run=cfg.dry_run,
            )
            state_store.record_alert(tstate, "email", now)
            logger.info("email alert %s", "sent" if ok else "FAILED")


def process_target(
    date_str: str, cfg: config_mod.Config, secrets: config_mod.Secrets, client, state: dict, now: datetime
) -> None:
    target_date = date.fromisoformat(date_str)
    tstate = state_store.get_target_state(state, date_str)

    if now.date() > target_date:
        logger.info("target date %s has passed; no longer monitoring", date_str)
        return

    if tstate.get("dismissed"):
        logger.info("target date %s dismissed by user; skipping", date_str)
        return

    if tstate.get("estimated_open_date") is None:
        est = scheduling.initial_open_date_estimate(target_date, cfg.booking_window_months_estimate)
        tstate["estimated_open_date"] = est.isoformat()
        logger.info("target %s: initial open-date estimate = %s", date_str, est)

    year_month = target_date.strftime("%Y-%m")

    try:
        month_map = client.get_month(year_month)
    except AcuityError as exc:
        _record_failure(cfg, secrets, tstate, now, date_str, exc)
        return

    tstate["consecutive_failures"] = 0
    tstate["failure_warning_sent"] = False

    previous_value = tstate["last_month_map"].get(date_str)
    current_value = month_map.get(date_str, False)
    flipped = scheduling.detect_flip(previous_value, current_value)
    if flipped:
        tstate["measured_open_date"] = now.date().isoformat()
        logger.info(
            "target %s: FLIP false->true observed on %s (real booking-window opening, "
            "replacing the 2-month estimate)",
            date_str, now.date(),
        )

    month_furthest = scheduling.furthest_bookable_date(month_map)
    existing_furthest = state.get("furthest_bookable_date")
    existing_furthest_date = date.fromisoformat(existing_furthest) if existing_furthest else None
    new_furthest = scheduling.combined_furthest_bookable_date(existing_furthest_date, month_furthest)
    if new_furthest:
        state["furthest_bookable_date"] = new_furthest.isoformat()

    # Slot-availability alerts are strictly more actionable than the
    # window-reached heads-up, so check/send them first: both compete for the
    # same per-channel rate-limit budget, and if a slot is already free the
    # moment the window opens, that's the alert that must win the slot.
    if current_value:
        try:
            raw_slots = client.get_times(date_str)
        except AcuityError as exc:
            _record_failure(cfg, secrets, tstate, now, date_str, exc)
            tstate["last_month_map"] = month_map
            return

        slots = scheduling.filter_slots_by_time_window(raw_slots, cfg.time_window_for(date_str), cfg.timezone)
        current_times = {s["time"] for s in slots}
        previous_times = set(tstate["seen_slot_times"])
        new_times, removed_times = state_store.diff_slot_times(previous_times, current_times)

        if current_times and (new_times or not previous_times):
            text, html = notifications.format_slot_alert(
                cfg.business_name, target_date, slots, cfg.timezone, cfg.booking_url
            )
            send_alert(cfg, secrets, tstate, now, text, html, subject=f"Haircut slot open {date_str}")
        elif removed_times and cfg.alert_on_removal:
            text, html = notifications.format_removal_alert(cfg.business_name, target_date, cfg.timezone, cfg.booking_url)
            send_alert(cfg, secrets, tstate, now, text, html, subject=f"Slots removed {date_str}")

        tstate["seen_slot_times"] = sorted(current_times)
    else:
        if tstate["seen_slot_times"] and cfg.alert_on_removal:
            text, html = notifications.format_removal_alert(cfg.business_name, target_date, cfg.timezone, cfg.booking_url)
            send_alert(cfg, secrets, tstate, now, text, html, subject=f"Slots removed {date_str}")
        tstate["seen_slot_times"] = []

    if new_furthest and new_furthest >= target_date and not tstate["window_open_alerted"]:
        text, html = notifications.format_window_open_alert(cfg.business_name, target_date, cfg.booking_url)
        send_alert(cfg, secrets, tstate, now, text, html, subject=f"Booking window reached {date_str}")
        tstate["window_open_alerted"] = True

    tstate["last_month_map"] = month_map


def _record_failure(
    cfg: config_mod.Config, secrets: config_mod.Secrets, tstate: dict, now: datetime, date_str: str, exc: Exception
) -> None:
    tstate["consecutive_failures"] += 1
    logger.error("target %s: fetch failed (%d consecutive): %s", date_str, tstate["consecutive_failures"], exc)
    if tstate["consecutive_failures"] >= 3 and not tstate["failure_warning_sent"]:
        text, html = notifications.format_warning_alert(
            f"{tstate['consecutive_failures']} consecutive polling failures for target {date_str}: {exc}"
        )
        send_alert(cfg, secrets, tstate, now, text, html, subject="Haircut monitor WARNING")
        tstate["failure_warning_sent"] = True


def maybe_send_heartbeat(cfg: config_mod.Config, secrets: config_mod.Secrets, state: dict, now: datetime) -> None:
    if not cfg.heartbeat:
        return
    last_ts = state.get("last_heartbeat_ts")
    if last_ts is not None and (now.timestamp() - last_ts) < 24 * 3600:
        return
    furthest = state.get("furthest_bookable_date")
    furthest_date = date.fromisoformat(furthest) if furthest else None
    text, html = notifications.format_heartbeat(furthest_date)
    # Heartbeat is informational and not per-target, so it bypasses per-target
    # rate limiting; still respects channel enable flags and dry_run.
    if cfg.channels.telegram and secrets.telegram_bot_token and secrets.telegram_chat_id:
        notifications.send_telegram(secrets.telegram_bot_token, secrets.telegram_chat_id, text, dry_run=cfg.dry_run)
    if cfg.channels.email and secrets.smtp_host and secrets.smtp_user and secrets.smtp_pass and secrets.email_to:
        notifications.send_email(
            secrets.smtp_host, secrets.smtp_port, secrets.smtp_user, secrets.smtp_pass,
            secrets.email_to, "Haircut monitor heartbeat", text, html, dry_run=cfg.dry_run,
        )
    state["last_heartbeat_ts"] = now.timestamp()


def effective_target_dates(cfg: config_mod.Config, state: dict) -> list[str]:
    """Dates to watch: config.yaml's seed list, unioned with any dates added
    at runtime (e.g. via the dashboard) that already have state entries.
    Lets the dashboard add/remove dates from a running process without a
    restart -- both it and the CLI daemon read this instead of cfg.target_dates
    directly."""
    return sorted(set(cfg.target_dates) | set(state["targets"].keys()))


def add_target_date(state: dict, date_str: str, cfg: Optional[config_mod.Config] = None) -> dict:
    """Start watching a new date. Validates the format and that it's not in
    the past; raises ValueError otherwise. Returns the (possibly pre-existing)
    target state, un-dismissing it if it had previously been dismissed. If
    `cfg` is given, seeds the initial open-date estimate immediately so a UI
    doesn't have to wait for the first poll to show something."""
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError as exc:
        raise ValueError(f"'{date_str}' is not a valid YYYY-MM-DD date") from exc
    if target_date < datetime.now(timezone.utc).date():
        raise ValueError(f"{date_str} is in the past")

    tstate = state_store.get_target_state(state, date_str)
    tstate["dismissed"] = False
    if cfg is not None and tstate.get("estimated_open_date") is None:
        est = scheduling.initial_open_date_estimate(target_date, cfg.booking_window_months_estimate)
        tstate["estimated_open_date"] = est.isoformat()
    return tstate


def remove_target_date(state: dict, date_str: str) -> bool:
    """Fully forget a date (history and all). Returns False if it wasn't tracked."""
    return state["targets"].pop(date_str, None) is not None


def run_once(cfg: config_mod.Config, secrets: config_mod.Secrets, client, state: dict) -> None:
    now = datetime.now(timezone.utc)
    logger.info("poll starting: now=%s UTC (%s)", now.isoformat(), cfg.timezone)
    for date_str in effective_target_dates(cfg, state):
        process_target(date_str, cfg, secrets, client, state, now)
    maybe_send_heartbeat(cfg, secrets, state, now)


def compute_next_sleep_seconds(cfg: config_mod.Config, state: dict, now: datetime) -> int:
    intervals = []
    for date_str in effective_target_dates(cfg, state):
        target_date = date.fromisoformat(date_str)
        if now.date() > target_date:
            continue
        tstate = state["targets"].get(date_str, {})
        if tstate.get("dismissed"):
            continue
        estimate_str = tstate.get("measured_open_date") or tstate.get("estimated_open_date")
        estimate_open_date = date.fromisoformat(estimate_str) if estimate_str else target_date
        slot_found = bool(tstate.get("seen_slot_times"))
        window_confirmed_open = tstate.get("measured_open_date") is not None
        seconds, phase = scheduling.compute_poll_interval_seconds(
            now, estimate_open_date, target_date, slot_found, cfg.poll, window_confirmed_open
        )
        logger.debug("target %s: next-poll phase=%s in %ds", date_str, phase, seconds)
        intervals.append(seconds)
    return min(intervals) if intervals else int(cfg.poll.far_hours * 3600)


def run_daemon(cfg: config_mod.Config, secrets: config_mod.Secrets, client, state: dict) -> None:
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    while not _shutdown_requested:
        try:
            run_once(cfg, secrets, client, state)
        except Exception:  # noqa: BLE001 - keep the daemon alive, log and continue
            logger.exception("unexpected error during poll; continuing")
        finally:
            state_store.save_state(cfg.state_file, state)

        if _shutdown_requested:
            break

        now = datetime.now(timezone.utc)
        sleep_seconds = compute_next_sleep_seconds(cfg, state, now)
        logger.info("sleeping %ds until next poll", sleep_seconds)
        slept = 0
        while slept < sleep_seconds and not _shutdown_requested:
            step = min(5, sleep_seconds - slept)
            time.sleep(step)
            slept += step

    logger.info("shutdown requested, exiting cleanly")


def dismiss_target(cfg: config_mod.Config, date_str: str) -> None:
    state = state_store.load_state(cfg.state_file)
    tstate = state_store.get_target_state(state, date_str)
    tstate["dismissed"] = True
    state_store.save_state(cfg.state_file, state)
    print(f"Dismissed alerts for {date_str}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml (default: config.yaml)")
    parser.add_argument("--once", action="store_true", help="single check then exit (for cron / GitHub Actions)")
    parser.add_argument("--dry-run", action="store_true", help="log notifications instead of sending them")
    parser.add_argument("--engine", choices=["requests", "browser"], default="requests")
    parser.add_argument("--dismiss", metavar="YYYY-MM-DD", help="stop alerting for this target date and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    cfg = config_mod.load_config(args.config)
    if args.dry_run:
        cfg.dry_run = True

    setup_logging(cfg.log_file, args.verbose)

    if args.dismiss:
        dismiss_target(cfg, args.dismiss)
        return 0

    secrets = config_mod.load_secrets()
    if cfg.channels.telegram and not cfg.dry_run and not (secrets.telegram_bot_token and secrets.telegram_chat_id):
        logger.warning("channels.telegram is enabled but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are not set")
    if cfg.channels.email and not cfg.dry_run and not (secrets.smtp_host and secrets.smtp_user and secrets.smtp_pass and secrets.email_to):
        logger.warning("channels.email is enabled but SMTP_*/EMAIL_TO are not fully set")

    client = build_client(args.engine, cfg)
    state = state_store.load_state(cfg.state_file)

    if args.once:
        run_once(cfg, secrets, client, state)
        state_store.save_state(cfg.state_file, state)
    else:
        run_daemon(cfg, secrets, client, state)

    return 0


if __name__ == "__main__":
    sys.exit(main())
