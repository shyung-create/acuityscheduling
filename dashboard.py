#!/usr/bin/env python3
"""Visual dashboard for the Acuity haircut availability monitor.

Single process: a background thread runs the same adaptive polling loop as
`monitor.py --daemon` (importing its functions directly, no duplicated
logic), while Flask serves a web UI to add/remove watched dates and see live
status. Notifications (Telegram/email) still fire exactly as configured in
config.yaml -- the dashboard is for managing *which* dates are being watched
and *seeing* their status, not a replacement for the alert channels.

Usage:
    python dashboard.py                       # http://127.0.0.1:5000
    python dashboard.py --port 8080 --dry-run
    python dashboard.py --engine=browser

Binds to 127.0.0.1 by default, no auth needed for pure-local use. As soon as
you bind to a non-loopback host (e.g. deploying to Fly.io/a VPS), the
DASHBOARD_PASSWORD env var becomes REQUIRED -- the mutation endpoints
(add/remove/dismiss a date, force a poll, send a test notification) would
otherwise be open to anyone who finds the URL. See README.md's Security /
Fly.io deployment sections.
"""
from __future__ import annotations

import argparse
import hmac
import logging
import os
import threading
import time
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from dateutil import parser as dateutil_parser
from flask import Flask, Response, jsonify, render_template, request

import config as config_mod
import monitor
import notifications
import scheduling
import state_store

logger = logging.getLogger("haircut_alarm.dashboard")

app = Flask(__name__)

_lock = threading.RLock()
_cfg: Optional[config_mod.Config] = None
_secrets: Optional[config_mod.Secrets] = None
_client = None
_state: dict = {}
_wake_event = threading.Event()
_stop_event = threading.Event()
_dashboard_user = "admin"
_dashboard_password: Optional[str] = None

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


@app.before_request
def _require_auth():
    if not _dashboard_password:
        return None  # no password configured -- fine for loopback-only local use
    auth = request.authorization
    ok = (
        auth is not None
        and hmac.compare_digest(auth.username or "", _dashboard_user)
        and hmac.compare_digest(auth.password or "", _dashboard_password)
    )
    if not ok:
        return Response(
            "Authentication required", 401, {"WWW-Authenticate": 'Basic realm="Haircut Dashboard"'}
        )
    return None


def init_app(
    config_path: str, engine: str, dry_run: bool, host: str, verbose: bool, interval: Optional[float] = None
) -> None:
    global _cfg, _secrets, _client, _state, _dashboard_user, _dashboard_password

    cfg = config_mod.load_config(config_path)
    if dry_run:
        cfg.dry_run = True
    if interval is not None:
        cfg.poll.fixed_minutes = interval
    monitor.setup_logging(cfg.log_file, verbose)

    secrets = config_mod.load_secrets()
    if cfg.channels.telegram and not cfg.dry_run and not (secrets.telegram_bot_token and secrets.telegram_chat_id):
        logger.warning("channels.telegram is enabled but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are not set")
    if cfg.channels.email and not cfg.dry_run and not (secrets.smtp_host and secrets.smtp_user and secrets.smtp_pass and secrets.email_to):
        logger.warning("channels.email is enabled but SMTP_*/EMAIL_TO are not fully set")

    _dashboard_user = os.environ.get("DASHBOARD_USER", "admin")
    _dashboard_password = os.environ.get("DASHBOARD_PASSWORD")
    if host not in LOOPBACK_HOSTS:
        if not _dashboard_password:
            raise SystemExit(
                f"Refusing to bind to non-loopback host {host!r} without DASHBOARD_PASSWORD set -- "
                "the dashboard's add/remove/poll endpoints would otherwise be open to anyone who finds "
                "the URL. Set DASHBOARD_PASSWORD (and optionally DASHBOARD_USER) and try again."
            )
        logger.warning("dashboard is bound to %s (not loopback) -- HTTP Basic Auth is enforced.", host)

    with _lock:
        _cfg = cfg
        _secrets = secrets
        _client = monitor.build_client(engine, cfg)
        _state = state_store.load_state(cfg.state_file)

    fixed_minutes = monitor.get_effective_fixed_minutes(_cfg, _state)
    if fixed_minutes is not None:
        logger.info(
            "fixed poll interval active: every %s minutes (adaptive far/near/hot/found schedule disabled)",
            fixed_minutes,
        )


def _poller_loop() -> None:
    while not _stop_event.is_set():
        with _lock:
            cfg, secrets, client, state = _cfg, _secrets, _client, _state
            # run_once must stay under the same lock as api_poll_now's --
            # otherwise a request that wakes this loop (e.g. adding a date,
            # which calls _wake_event.set()) can run concurrently with this
            # background cycle on the same mutable `state` dict. Both sides
            # would independently see "no previously-seen slot" and both
            # send the same alert -- this is exactly how a real deployment
            # produced a duplicate Telegram message.
            try:
                monitor.run_once(cfg, secrets, client, state)
            except Exception:  # noqa: BLE001 - keep the background thread alive
                logger.exception("unexpected error during background poll; continuing")
            finally:
                state_store.save_state(cfg.state_file, state)

            now = datetime.now(timezone.utc)
            sleep_seconds = monitor.compute_next_sleep_seconds(cfg, state, now)
        logger.info("dashboard poller sleeping %ds", sleep_seconds)

        _wake_event.clear()
        _wake_event.wait(timeout=sleep_seconds)


def start_background_thread() -> threading.Thread:
    t = threading.Thread(target=_poller_loop, name="haircut-poller", daemon=True)
    t.start()
    return t


def _target_view(date_str: str, tstate: dict, cfg: config_mod.Config, now: datetime) -> dict:
    target_date = date.fromisoformat(date_str)
    days_until_target = (target_date - now.date()).days

    measured_open = tstate.get("measured_open_date")
    estimated_open = tstate.get("estimated_open_date")
    current_times = sorted(tstate.get("seen_slot_times", []))

    if tstate.get("dismissed"):
        badge = "dismissed"
    elif days_until_target < 0:
        badge = "passed"
    elif current_times:
        badge = "slot_available"
    elif tstate.get("consecutive_failures", 0) >= 3:
        badge = "failing"
    elif measured_open:
        badge = "window_open_no_slot"
    else:
        badge = "watching"

    display_times = []
    if current_times:
        tz = ZoneInfo(cfg.timezone)
        for iso in current_times:
            # Same Acuity "-0700"-offset format as notifications.py/
            # scheduling.py -- datetime.fromisoformat() chokes on it below
            # Python 3.11 (Ubuntu 22.04's default python3 is 3.10).
            display_times.append(dateutil_parser.isoparse(iso).astimezone(tz).strftime("%H:%M"))

    time_window = monitor.resolve_time_window(cfg, tstate, date_str)

    return {
        "date": date_str,
        "weekday": target_date.strftime("%a"),
        "days_until_target": days_until_target,
        "badge": badge,
        "estimated_open_date": estimated_open,
        "measured_open_date": measured_open,
        "current_times": display_times,
        "consecutive_failures": tstate.get("consecutive_failures", 0),
        "window_open_alerted": tstate.get("window_open_alerted", False),
        "dismissed": bool(tstate.get("dismissed")),
        "last_alert_ts": tstate.get("last_alert_ts", {}),
        "time_window": {"start": time_window.start, "end": time_window.end} if time_window.enabled else None,
    }


@app.route("/")
def index():
    return render_template("dashboard.html", business_name=_cfg.business_name if _cfg else "")


@app.route("/api/status")
def api_status():
    with _lock:
        cfg, state = _cfg, _state
        now = datetime.now(timezone.utc)
        dates = monitor.effective_target_dates(cfg, state)
        targets = [_target_view(d, state_store.get_target_state(state, d), cfg, now) for d in dates]
        furthest = state.get("furthest_bookable_date")

    return jsonify(
        {
            "now": now.isoformat(),
            "timezone": cfg.timezone,
            "business_name": cfg.business_name,
            "booking_url": cfg.booking_url,
            "dry_run": cfg.dry_run,
            "furthest_bookable_date": furthest,
            "targets": targets,
        }
    )


def _validate_time_window_dict(tw: dict) -> dict:
    """Normalizes a {"start": .., "end": ..} dict, raising ValueError if only
    one side is given. {} (or both blank) means "any time"."""
    start, end = tw.get("start") or None, tw.get("end") or None
    if bool(start) != bool(end):
        raise ValueError("time_window needs both start and end, or neither (for any time)")
    return {"start": start, "end": end} if start else {}


@app.route("/api/targets", methods=["POST"])
def api_add_targets():
    payload = request.get_json(silent=True) or {}
    raw = payload.get("dates") or payload.get("date") or ""
    candidates = [d.strip() for d in raw.replace(",", "\n").splitlines() if d.strip()]

    time_window = None  # None = don't set an override, leave any existing one untouched
    if "time_window" in payload:
        try:
            time_window = _validate_time_window_dict(payload["time_window"] or {})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    added, errors = [], {}
    with _lock:
        for date_str in candidates:
            try:
                monitor.add_target_date(_state, date_str, _cfg, time_window=time_window)
                added.append(date_str)
            except ValueError as exc:
                errors[date_str] = str(exc)
        state_store.save_state(_cfg.state_file, _state)
    _wake_event.set()  # let the poller re-evaluate immediately with the new date(s)

    status = 200 if added else 400
    return jsonify({"added": added, "errors": errors}), status


@app.route("/api/targets/<date_str>/time-window", methods=["POST"])
def api_set_target_time_window(date_str: str):
    payload = request.get_json(silent=True) or {}
    try:
        time_window = _validate_time_window_dict(payload.get("time_window") or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    with _lock:
        if date_str not in _state["targets"]:
            return jsonify({"error": f"{date_str} is not being watched"}), 404
        tstate = state_store.get_target_state(_state, date_str)
        tstate["time_window_override"] = time_window  # always explicit here, even {} ("any time")
        state_store.save_state(_cfg.state_file, _state)
    _wake_event.set()
    return jsonify({"date": date_str, "time_window": time_window or None})


@app.route("/api/targets/<date_str>", methods=["DELETE"])
def api_remove_target(date_str: str):
    with _lock:
        if date_str in _cfg.target_dates:
            # effective_target_dates() unions state.json's targets with
            # config.yaml's seed list on every refresh -- removing a
            # config.yaml date from state.json alone doesn't stick, it just
            # gets silently re-added (with a fresh, history-less state) on
            # the next poll or /api/status call. Refuse clearly instead of
            # pretending to succeed.
            return jsonify({
                "removed": False,
                "error": (
                    f"{date_str} is a seed date in config.yaml -- removing it here won't stick, it "
                    "gets re-added on the next refresh. Use Dismiss to stop alerts instead, or edit "
                    "config.yaml directly and restart the service to remove it permanently."
                ),
            }), 409
        removed = monitor.remove_target_date(_state, date_str)
        state_store.save_state(_cfg.state_file, _state)
    return jsonify({"removed": removed}), (200 if removed else 404)


@app.route("/api/targets/<date_str>/dismiss", methods=["POST"])
def api_dismiss_target(date_str: str):
    with _lock:
        tstate = state_store.get_target_state(_state, date_str)
        tstate["dismissed"] = True
        state_store.save_state(_cfg.state_file, _state)
    return jsonify({"dismissed": date_str})


@app.route("/api/targets/<date_str>/undismiss", methods=["POST"])
def api_undismiss_target(date_str: str):
    with _lock:
        try:
            monitor.add_target_date(_state, date_str, _cfg)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        state_store.save_state(_cfg.state_file, _state)
    _wake_event.set()
    return jsonify({"undismissed": date_str})


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    with _lock:
        cfg, state = _cfg, _state
        fixed_minutes = monitor.get_effective_fixed_minutes(cfg, state)
    return jsonify(
        {
            "mode": "fixed" if fixed_minutes is not None else "adaptive",
            "fixed_minutes": fixed_minutes,
            "min_minutes": cfg.poll.min_minutes,
            "adaptive": {
                "far_hours": cfg.poll.far_hours,
                "near_minutes": cfg.poll.near_minutes,
                "hot_minutes": cfg.poll.hot_minutes,
                "found_hours": cfg.poll.found_hours,
            },
        }
    )


@app.route("/api/settings", methods=["POST"])
def api_update_settings():
    payload = request.get_json(silent=True) or {}
    mode = payload.get("mode")

    with _lock:
        if mode == "adaptive":
            monitor.set_fixed_minutes_override(_state, None)
        elif mode == "fixed":
            try:
                minutes = float(payload.get("minutes"))
            except (TypeError, ValueError):
                return jsonify({"error": "minutes must be a number"}), 400
            if minutes <= 0:
                return jsonify({"error": "minutes must be positive"}), 400
            monitor.set_fixed_minutes_override(_state, minutes)
        else:
            return jsonify({"error": "mode must be 'adaptive' or 'fixed'"}), 400
        state_store.save_state(_cfg.state_file, _state)
    _wake_event.set()  # apply the new cadence immediately instead of waiting out the old sleep
    return jsonify({"ok": True})


@app.route("/api/poll-now", methods=["POST"])
def api_poll_now():
    with _lock:
        monitor.run_once(_cfg, _secrets, _client, _state)
        state_store.save_state(_cfg.state_file, _state)
    return jsonify({"polled": True})


@app.route("/api/test-notification", methods=["POST"])
def api_test_notification():
    text = f"✅ Test notification from the haircut availability dashboard ({_cfg.business_name})."
    html = f"<p>{text}</p>"
    results = {}
    with _lock:
        cfg, secrets = _cfg, _secrets
    if cfg.channels.telegram:
        if secrets.telegram_bot_token and secrets.telegram_chat_id:
            results["telegram"] = notifications.send_telegram(
                secrets.telegram_bot_token, secrets.telegram_chat_id, text, dry_run=cfg.dry_run
            )
        else:
            results["telegram"] = "not_configured"
    if cfg.channels.email:
        if secrets.smtp_host and secrets.smtp_user and secrets.smtp_pass and secrets.email_to:
            results["email"] = notifications.send_email(
                secrets.smtp_host, secrets.smtp_port, secrets.smtp_user, secrets.smtp_pass,
                secrets.email_to, "Haircut monitor test notification", text, html, dry_run=cfg.dry_run,
            )
        else:
            results["email"] = "not_configured"
    return jsonify(results)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--engine", choices=["requests", "browser"], default="requests")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument(
        "--interval", type=float, metavar="MINUTES",
        help="poll at this fixed interval (minutes) instead of the adaptive far/near/hot/found "
             "schedule. Overrides poll.fixed_minutes in config.yaml if both are set.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    init_app(args.config, args.engine, args.dry_run, args.host, args.verbose, args.interval)
    start_background_thread()
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
