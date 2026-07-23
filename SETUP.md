# Deploying the haircut availability dashboard (systemd, no Docker)

`dashboard.py` runs `monitor.py`'s adaptive polling loop in a background
thread and serves a small Flask web UI for adding/removing watched dates,
adjusting the poll interval, and seeing live status -- one process, one
systemd service. This is meant for a small Always Free-tier VM (e.g. Oracle
Cloud, 1GB RAM); polling never needs an inbound endpoint, so the only
network requirement is outbound HTTPS to Acuity's public availability
endpoints and the Telegram Bot API.

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

`requirements-dashboard.txt` pulls in `requirements.txt` plus Flask:

```bash
sudo -u acuityalarm python3 -m venv /opt/acuity-alarm/venv
sudo -u acuityalarm /opt/acuity-alarm/venv/bin/pip install -r /opt/acuity-alarm/requirements-dashboard.txt
```

## 3. Configure secrets

Copy the example and fill in real values -- **never commit the real file**:

```bash
sudo -u acuityalarm cp /opt/acuity-alarm/.env.example /opt/acuity-alarm/.env
sudo -u acuityalarm nano /opt/acuity-alarm/.env
```

Required:

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` -- see the comments already in
  `.env.example` for how to get these from @BotFather.

Only needed if you're exposing the dashboard beyond an SSH tunnel (see
"Reaching the dashboard" in `infra/README.md`):

- `DASHBOARD_PASSWORD` (and optionally `DASHBOARD_USER`) for a real login
  prompt, or `DASHBOARD_TRUST_NETWORK_LAYER=1` if something else (e.g.
  Tailscale) is the only thing meant to gate access.

Lock the file down -- it holds live credentials and is read by
`EnvironmentFile=` as root before systemd drops privileges to `acuityalarm`,
so `acuityalarm:acuityalarm` ownership with `600` is enough (root doesn't
need a separate allowance):

```bash
sudo chown acuityalarm:acuityalarm /opt/acuity-alarm/.env
sudo chmod 600 /opt/acuity-alarm/.env
```

## 4. Configure watched dates

`config.yaml`'s `target_dates` is empty by default -- add dates through the
dashboard's web UI instead (persisted in `state.json`, not the config file;
see the "config.yaml vs. dashboard-added dates" note in `RUNBOOK.md` for
what that trade-off means). If you want specific dates to always be watched
regardless of what the dashboard shows, add them to `config.yaml` directly
and redeploy.

## 5. Install and enable the systemd units

```bash
sudo cp /opt/acuity-alarm/systemd/haircut-dashboard.service /etc/systemd/system/
sudo cp /opt/acuity-alarm/systemd/heartbeat-check.service /opt/acuity-alarm/systemd/heartbeat-check.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now haircut-dashboard.service
sudo systemctl enable --now heartbeat-check.timer
```

`heartbeat-check.timer` is the dead-man's-switch: every 30 minutes it checks
whether the poller has completed a cycle recently, over a code path that
shares nothing with the Acuity/Telegram code `dashboard.py` uses (see
`RUNBOOK.md`), and sends one Telegram alert if it looks stuck. It runs on
the system `python3`, not the app's venv (it's stdlib-only), so a broken
venv can't silence it. Unlike a flat "no update in N minutes" threshold, it
compares against a `next_expected_by` timestamp the poller writes itself --
this adapts automatically to whichever adaptive-polling phase is active
(cycles can legitimately be hours apart in the "far" phase), so it won't
false-alarm just because nothing's happening on a quiet day.

`haircut-dashboard.service` is a long-running process (`Type=simple`), not
a oneshot -- enabling it starts the whole dashboard + poller and keeps it
running. `heartbeat-check.service` *is* meant to be triggered by its timer,
not started directly on a recurring basis -- you can still fire one check
manually any time:

```bash
sudo systemctl start heartbeat-check.service
```

## 6. Cap journald so logs can't fill the boot volume

Everything logs to journald only -- there's no file-based logging in this
deployment. Install the provided cap so persistent logs can't slowly eat a
small Always Free boot volume:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo cp /opt/acuity-alarm/systemd/journald-acuity-alarm.conf /etc/systemd/journald.conf.d/acuity-alarm.conf
sudo systemctl restart systemd-journald
```

This caps persistent journal storage at 200M and runtime (tmpfs) storage at
50M, system-wide. Check current usage any time with `journalctl --disk-usage`.

## 7. Verify

```bash
sudo systemctl status haircut-dashboard.service
sudo journalctl -u haircut-dashboard.service -f
```

A healthy log shows `poll starting`/`dashboard poller sleeping Ns` cycling,
and (once `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are set) `telegram alert
sent` whenever a watched date's status changes.

Confirm the dead-man's-switch is wired up too:

```bash
systemctl status heartbeat-check.timer
sudo systemctl start heartbeat-check.service   # run it once by hand
journalctl -u heartbeat-check.service -n 5 --no-pager
```

It should log `heartbeat-check: OK (...)` once the dashboard has completed
at least one poll cycle. See `RUNBOOK.md` if it ever alerts.

## Reaching the dashboard

See `infra/README.md`'s "Reaching the dashboard" section for the full set of
options (SSH tunnel, Tailscale, or opening a public port) and their
trade-offs -- this repo has been run with all three at different points.

## Updating the poll interval

Set it from the dashboard's "Poll interval" panel (adaptive vs. a fixed
number of minutes) -- takes effect on the next cycle, no redeploy needed.
`config.yaml`'s `poll:` block only sets the *defaults* for the adaptive
far/near/hot/found phases; a dashboard override takes precedence (see
`monitor.py`'s `get_effective_fixed_minutes`).
