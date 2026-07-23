# Runbook: what to check if alerts stop

This is a single-purpose bot with no on-call behind it. If you got a "looks
stuck" heartbeat alert, the dashboard won't load, or you just haven't seen
any Telegram messages in a while and aren't sure if that's because nothing
happened or because something's broken, work through this in order.

## 1. Is the service still running at all?

```bash
ssh deploy@<reserved-ip>
sudo systemctl status haircut-dashboard.service
```

- **Not `active (running)`**: `sudo systemctl restart haircut-dashboard.service`,
  then go to step 2 to see why it stopped.
- **Active, but the dashboard won't load in your browser**: that's likely
  Tailscale, not the service -- see step 5.

## 2. What does the service log say?

```bash
sudo journalctl -u haircut-dashboard.service -n 50 --no-pager
sudo journalctl -u haircut-dashboard.service -f          # tail live
```

Common causes, in the order worth checking:

- **`ValueError: Invalid isoformat string: '...-0700'`** -- this exact bug
  was fixed once already (Acuity's colonless UTC offset breaks
  `datetime.fromisoformat()` on Python < 3.11); if it's back, the venv or
  code is out of date. See "clean restart" below.
- **`channels.telegram is enabled but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID
  are not set`** -- `.env` is missing, misplaced, or those two vars are
  blank. Check `sudo cat /opt/acuity-alarm/.env` (don't paste the actual
  token/chat ID anywhere) and fill them in per `.env.example`'s comments,
  then `sudo systemctl restart haircut-dashboard.service`.
- **Telegram send logged as `FAILED`** -- most often the bot token was
  regenerated via @BotFather, or the chat ID changed (e.g. you deleted/
  recreated the chat with the bot). Get a fresh token/chat ID and update
  `.env`.
- **`ModuleNotFoundError` or similar Python import error** -- the venv is
  missing or out of date; see "clean restart" below.
- **Repeated "unexpected error during background poll"** -- the poller
  thread caught an exception and kept going (by design, so one bad cycle
  doesn't kill the whole dashboard), but if it's the *same* traceback every
  cycle, that's a real bug -- check whether it's a known one already fixed
  upstream (`git log`), and if not, that's a genuine new issue to debug.

## 3. Confirm a poll cycle actually completes cleanly

Either wait for the next cycle in the log, or force one immediately from
the dashboard's "Poll now" button (or `curl -s -X POST http://127.0.0.1:5000/api/poll-now`
on the server itself), then check the log for a clean cycle with no
traceback.

## 4. Did the heartbeat alert actually fire, or is the heartbeat check itself broken?

The heartbeat check is deliberately standalone (no shared code with the
Acuity/Telegram code the dashboard uses), so check it independently:

```bash
sudo journalctl -u heartbeat-check.service -n 20 --no-pager
systemctl status heartbeat-check.timer
cat /opt/acuity-alarm/heartbeat.txt   # {"last_poll": ..., "next_expected_by": ...}
```

If it looks fresh but you got an alert anyway, or looks stale but no alert
arrived, re-run it by hand:

```bash
sudo systemctl start heartbeat-check.service
sudo journalctl -u heartbeat-check.service -n 20 --no-pager
```

It prints exactly what it decided and why (`heartbeat-check: OK (...)`,
`... stale, alert sent`, or `... TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not
set`). Note that `next_expected_by` adapts to the adaptive polling phase --
a multi-hour gap between polls during the "far" phase is normal, not stuck;
only trust an alert that says it's actually past `next_expected_by`.

## 5. Dashboard won't load, but the service is running

- **Via Tailscale**: check `tailscale status` on both the server and the
  device you're browsing from -- a device showing `offline` in that list
  won't be able to reach anything on the tailnet, regardless of whether the
  server itself is fine. Also confirm `sudo tailscale serve status` still
  shows the proxy config (`https://<machine>.<tailnet>.ts.net -> http://127.0.0.1:5000`).
- **Via SSH tunnel**: confirm the tunnel is actually open in a terminal
  window (`ssh -L 5000:127.0.0.1:5000 ...`) -- closing that window drops it.
- Either way, `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/`
  run **on the server itself** tells you whether the app is responding at
  all, independent of how you're trying to reach it.

## 6. "Remove" on a watched date does nothing

If a date is seeded in `config.yaml`'s `target_dates`, the dashboard's
"Remove" button will refuse with a clear error instead of silently
reappearing (this used to be a real bug -- fixed). Two different things,
easy to mix up:
- **Dismiss** stops alerts for a date but keeps it visible with a
  "Re-enable" button -- it doesn't touch `config.yaml`.
- **Permanently removing** a `config.yaml`-seeded date means editing the
  file itself (`git commit`/push/pull/restart) -- the dashboard alone can't
  do it. Dates added *through* the dashboard (not in `config.yaml`) don't
  have this problem -- "Remove" deletes them outright, but only from
  `state.json` on this one server, not anywhere durable (see the
  config.yaml-vs-dashboard trade-off discussion from when this was set up).

## 7. Clean restart

If you've fixed `.env`, updated the venv, or just want a known-good state:

```bash
sudo -u acuityalarm bash -c '
  cd /opt/acuity-alarm
  rm -rf venv
  python3 -m venv venv
  venv/bin/pip install --upgrade pip
  venv/bin/pip install -r requirements-dashboard.txt
'

sudo systemctl daemon-reload
sudo systemctl restart haircut-dashboard.service
sudo systemctl restart heartbeat-check.timer
sudo journalctl -u haircut-dashboard.service -n 20 --no-pager   # confirm it comes up clean
```

If it's still broken after this, `state.json` may be worth inspecting
directly (`cat /opt/acuity-alarm/state.json`), but that's past what this
runbook covers -- at that point you're debugging the code, not the
deployment.
