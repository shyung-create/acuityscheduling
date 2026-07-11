# Runbook: what to check if alerts stop

This is a single-purpose bot with no on-call behind it. If you got a "looks
stuck" heartbeat alert, or you just haven't seen any Telegram messages in a
while and aren't sure if that's because nothing happened or because
something's broken, work through this in order.

## 1. Is the poller still running at all?

```bash
ssh deploy@<reserved-ip>
systemctl status acuity-alarm.timer
systemctl list-timers acuity-alarm.timer   # confirms it's scheduled and shows last/next run
```

- **Timer inactive/disabled**: `sudo systemctl enable --now acuity-alarm.timer`
- **Timer active but "last run" looks old**: the service itself is failing
  to start -- go to step 2.

## 2. What does the service log say?

```bash
journalctl -u acuity-alarm.service -n 50 --no-pager
journalctl -u acuity-alarm.service -f          # tail live
```

Common causes, in the order worth checking:

- **`configuration error: missing required environment variable(s): ...`**
  -- `.env` is missing, misplaced, or a required var was dropped. Check
  `/opt/acuity-alarm/.env` exists and `cat` (as root or `acuityalarm`) shows
  all of `ACUITY_USER_ID`, `ACUITY_API_KEY`, `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`.
- **`Acuity auth failed (HTTP 401/403)`** -- the Acuity API key was
  regenerated/revoked in Acuity's Business Settings -> Integrations -> API,
  or `ACUITY_USER_ID` doesn't match it. Get a fresh key/user ID from Acuity
  and update `.env` (see `DEPLOY.md` step 3), then
  `sudo systemctl start acuity-alarm.service` to confirm it clears.
- **Telegram send logged as `FAILED`** -- most often the bot token was
  regenerated via @BotFather, or the chat ID changed (e.g. you deleted/
  recreated the chat with the bot). Get a fresh token/chat ID (see
  `.env.example`'s Telegram setup comments) and update `.env`.
- **`ModuleNotFoundError` or similar Python import error** -- the venv is
  missing or out of date; see "clean restart" below.
- **Nothing in the log at all, ever** -- the timer isn't actually
  triggering the service; re-check step 1, and confirm
  `/etc/systemd/system/acuity-alarm.timer` and `.service` match what's in
  this repo's `systemd/` (a manual step -- CI does not deploy these files,
  see `DEPLOY.md`).

## 3. Did the heartbeat alert actually fire, or is the heartbeat check itself broken?

The heartbeat check is deliberately standalone (no shared code with the
Acuity/Telegram client used above), so check it independently:

```bash
journalctl -u heartbeat-check.service -n 20 --no-pager
systemctl status heartbeat-check.timer
cat /opt/acuity-alarm/heartbeat.txt   # timestamp of the last successful acuity-alarm cycle
```

If `heartbeat.txt` is fresh (updated within the last ~20 min) but you got an
alert anyway, or the file is stale but no alert arrived, re-run it by hand:

```bash
sudo systemctl start heartbeat-check.service
journalctl -u heartbeat-check.service -n 20 --no-pager
```

It prints exactly what it decided and why (`heartbeat-check: OK (...)`,
`... stale heartbeat, alert sent`, or `... TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID
not set`).

## 4. Manually trigger one cycle to confirm things actually work

```bash
sudo systemctl start acuity-alarm.service
journalctl -u acuity-alarm.service -n 20 --no-pager
```

A healthy run ends with `poll cycle complete: N alert(s) sent` (N is often
`0` -- that's normal).

## 5. Clean restart

If you've fixed `.env`, updated the venv, or just want a known-good state:

```bash
# Re-create the venv from the pinned requirements (mirrors what the deploy
# pipeline does -- see .github/workflows/deploy.yml)
sudo -u acuityalarm bash -c '
  cd /opt/acuity-alarm
  rm -rf venv
  python3 -m venv venv
  venv/bin/pip install --upgrade pip
  venv/bin/pip install -r requirements-appointment-alarm.txt
'

sudo systemctl daemon-reload
sudo systemctl restart acuity-alarm.timer
sudo systemctl restart heartbeat-check.timer
sudo systemctl start acuity-alarm.service   # confirm one cycle succeeds right away
```

If it's still broken after this, the state DB or heartbeat file may be
worth inspecting directly (`sqlite3 /opt/acuity-alarm/state.db`), but that's
past what this runbook covers -- at that point you're debugging the code,
not the deployment.
