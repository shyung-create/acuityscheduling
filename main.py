#!/usr/bin/env python3
"""Entrypoint for the Acuity appointment alarm (owner-side, polling-based).

Runs exactly one poll cycle and exits -- no internal sleep loop. Intended to
be invoked on a schedule by systemd (see systemd/acuity-alarm.timer) or cron;
run it by hand any time to check right now:

    python main.py
    python main.py -v          # verbose logging
"""
from __future__ import annotations

import argparse
import logging
import sys

import appointment_monitor
import appointment_store
from appointment_client import AcuityAppointmentsClient
from appointment_config import ConfigError, load_config

logger = logging.getLogger("appointment_alarm")


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    try:
        cfg = load_config()
    except ConfigError as exc:
        logger.error("configuration error: %s", exc)
        return 1

    client = AcuityAppointmentsClient(user_id=cfg.acuity_user_id, api_key=cfg.acuity_api_key)
    conn = appointment_store.connect(cfg.state_db_path)
    try:
        events = appointment_monitor.run_cycle(client, conn, cfg)
    finally:
        conn.close()

    logger.info("poll cycle complete: %d alert(s) sent", len(events))
    return 0


if __name__ == "__main__":
    sys.exit(main())
