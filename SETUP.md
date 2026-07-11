# Deploying the appointment alarm (systemd, no Docker)

`main.py` runs one poll cycle (fetch appointments, diff, alert, exit).
`acuity-alarm.timer` controls how often that happens (every 5 minutes,
defined once in `systemd/acuity-alarm.timer`'s `OnUnitActiveSec` -- not
duplicated anywhere else). This is meant for a small Always Free-tier VM
(e.g. Oracle Cloud, 1GB RAM) with no public domain or open ports, since
polling never needs an inbound endpoint.

## 1. Create a dedicated user and directory

`--no-create-home` plus a manual `mkdir`/`chown` (rather than
`useradd --create-home`) avoids `useradd` pre-populating `/opt/acuity-alarm`
with skeleton dotfiles (`.bashrc`, `.profile`, ...) -- `git clone` refuses to
clone into a non-empty directory, so `--create-home` here would just make
the next command fail:

```bash
sudo useradd --system --no-create-home --home-dir /opt/acuity-alarm --shell /usr/sbin/nologin acuityalarm
sudo mkdir -p /opt/acuity-alarm
sudo chown acuityalarm:acuityalarm /opt/acuity-alarm
sudo -u acuityalarm git clone <this-repo-url> /opt/acuity-alarm
```

## 2. Create the venv and install dependencies

```bash
sudo -u acuityalarm python3 -m venv /opt/acuity-alarm/venv
sudo -u acuityalarm /opt/acuity-alarm/venv/bin/pip install -r /opt/acuity-alarm/requirements-appointment-alarm.txt
```

## 3. Configure secrets

Copy the example and fill in real values -- **never commit the real file**:

```bash
sudo -u acuityalarm cp /opt/acuity-alarm/.env.example /opt/acuity-alarm/.env
sudo -u acuityalarm nano /opt/acuity-alarm/.env
```

Required for the appointment alarm specifically:

- `ACUITY_USER_ID`, `ACUITY_API_KEY` -- Acuity: Business Settings ->
  Integrations -> API.
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` -- see the comments already in
  `.env.example`.
- Leave `POLL_WINDOW_DAYS`, `REMINDER_THRESHOLDS_MINUTES`,
  `APPOINTMENT_FAILURE_ALERT_THRESHOLD`, `APPOINTMENT_STATE_DB_PATH`,
  `HEARTBEAT_FILE_PATH`, `HEARTBEAT_STALE_MINUTES` at their defaults unless
  you have a reason to change them.

Lock the file down -- it holds live credentials and is read by
`EnvironmentFile=` as root before systemd drops privileges to `acuityalarm`,
so `acuityalarm:acuityalarm` ownership with `600` is enough (root doesn't
need a separate allowance):

```bash
sudo chown acuityalarm:acuityalarm /opt/acuity-alarm/.env
sudo chmod 600 /opt/acuity-alarm/.env
```

## 4. Install and enable the systemd units

```bash
sudo cp /opt/acuity-alarm/systemd/acuity-alarm.service /opt/acuity-alarm/systemd/acuity-alarm.timer /etc/systemd/system/
sudo cp /opt/acuity-alarm/systemd/heartbeat-check.service /opt/acuity-alarm/systemd/heartbeat-check.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now acuity-alarm.timer
sudo systemctl enable --now heartbeat-check.timer
```

`heartbeat-check.timer` is the dead-man's-switch: every 30 minutes it checks
whether `acuity-alarm.timer` has completed a cycle recently, over a code
path that shares nothing with the Acuity/Telegram client `main.py` uses (see
`RUNBOOK.md`), and sends one Telegram alert if the poller looks stuck. It
runs on the system `python3`, not the app's venv (it's stdlib-only), so a
broken venv can't silence it.

Do **not** `enable` or `start` `acuity-alarm.service` / `heartbeat-check.service`
directly on a recurring basis -- the timers are what's enabled; they
activate their services on schedule. You can still fire one cycle manually
at any time with:

```bash
sudo systemctl start acuity-alarm.service
sudo systemctl start heartbeat-check.service
```

## 4b. Cap journald so logs can't fill the boot volume

Both services log to journald only -- there's no file-based logging in this
deployment. Install the provided cap so persistent logs can't slowly eat a
small Always Free boot volume:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo cp /opt/acuity-alarm/systemd/journald-acuity-alarm.conf /etc/systemd/journald.conf.d/acuity-alarm.conf
sudo systemctl restart systemd-journald
```

This caps persistent journal storage at 200M and runtime (tmpfs) storage at
50M, system-wide -- fine for a box that runs only this one service. Check
current usage any time with `journalctl --disk-usage`.

## 5. Verify

```bash
systemctl status acuity-alarm.timer      # active, shows next trigger time
systemctl list-timers acuity-alarm.timer # confirms the 5-minute schedule
journalctl -u acuity-alarm.service -f    # tail logs as cycles run
```

A healthy cycle logs `poll cycle complete: N alert(s) sent` (N is often 0 --
that's normal, it just means nothing changed). The very first run logs a
backfill message and will not send `new_booking` alerts for appointments
that already existed before the alarm was set up; cancellations, reschedules,
and reminders still work normally from the first run onward.

If `main.py` exits non-zero (e.g. `ACUITY_API_KEY` missing or wrong),
`journalctl -u acuity-alarm.service` shows the error, and systemd retries
per `Restart=on-failure` / `RestartSec=30` in the service unit.

Confirm the dead-man's-switch is wired up too:

```bash
systemctl status heartbeat-check.timer
sudo systemctl start heartbeat-check.service   # run it once by hand
journalctl -u heartbeat-check.service -n 5 --no-pager
```

It should log `heartbeat-check: OK (N min old)` once `acuity-alarm.timer`
has completed at least one cycle. See `RUNBOOK.md` if it ever alerts.

## Updating the poll interval or reminder thresholds

- **Poll interval**: edit `OnUnitActiveSec=` in
  `systemd/acuity-alarm.timer`, redeploy the file to
  `/etc/systemd/system/acuity-alarm.timer`, then
  `sudo systemctl daemon-reload && sudo systemctl restart acuity-alarm.timer`.
- **Reminder thresholds**: edit `REMINDER_THRESHOLDS_MINUTES` in `.env` (no
  redeploy needed -- it's read fresh each cycle).
