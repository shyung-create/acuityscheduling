# Haircut Availability Alarm (E sharp hair / Acuity Scheduling)

Watches `https://esharphair.as.me/schedule/dc1e29cb/appointment/82222707` for
a bookable timeslot on your target date(s) and alerts you via Telegram
and/or email the moment one appears.

> **Live verification not yet run.** This was built in a sandboxed session
> whose network policy blocks outbound requests to `esharphair.as.me`, so the
> two `curl` verification commands below have **not** been executed against
> the real site yet. The code is written against the endpoint shapes given in
> the spec (and matches the fixtures in `tests/fixtures/`), but you should run
> the verification step yourself before trusting it unattended:
>
> ```bash
> curl -s "https://esharphair.as.me/api/scheduling/v1/availability/month?owner=dc1e29cb&appointmentTypeId=82222707&calendarId=any&timezone=America%2FLos_Angeles&month=2026-09" | python3 -m json.tool
> curl -s "https://esharphair.as.me/api/scheduling/v1/availability/times?owner=dc1e29cb&appointmentTypeId=82222707&calendarId=any&startDate=2026-09-15&timezone=America%2FLos_Angeles" | python3 -m json.tool
> ```
>
> Confirm: (a) the `month` param selects arbitrary future months, (b) a month
> ~2 months out from today is entirely `false` or shows a partial cutoff --
> this pins down the shop's real rolling booking-window length. Report back
> what you see; `monitor.py` will self-correct its estimate the first time it
> observes a real false→true flip regardless (see "How it works" below), but
> confirming the shapes up front catches API changes early.

## How it works

1. `initial_open_date_estimate()` computes `target_date - ~2 months` using
   `dateutil.relativedelta` (handles month-end/leap-year edge cases) purely
   to seed the polling cadence.
2. Every poll calls `GET /availability/month?...&month=YYYY-MM` for the
   target date's month -- one cheap call covering the whole month.
3. The first time a target date flips from `false` to `true` between two
   polls, that's treated as ground truth for when the window really opened
   (`state.json`'s `measured_open_date`), replacing the 2-month guess.
4. When the target date shows `true`, `GET /availability/times?startDate=...`
   is called for the specific times, filtered to your optional time window,
   and diffed against previously-seen times to decide whether to alert.
5. A separate "furthest bookable date" is tracked across polls; the first
   time it reaches or passes your target date, you get a one-time heads-up
   even if no slot is free yet (fully-booked-instantly is useful to know).
6. Polling interval adapts: far (6h) -> near (30m, within 3 days of the
   estimate) -> hot (5m, on the estimated date + the following 24h) -> found
   (2h, after a slot has been alerted). See `scheduling.py` for the exact
   state machine and `tests/test_scheduling.py` for the edge cases it's
   tested against.

Availability can flicker due to Acuity's "Look Busy" (hides some open slots)
and "Minimize Gaps" (hides slots that would create awkward gaps) settings --
this is expected, not a bug.

## Setup

### 1. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Telegram bot (preferred channel)

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`,
   follow the prompts. You'll get a bot token like `123456:ABC-DEF...`.
2. Send any message to your new bot (search for its username and say hi).
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and
   read the chat ID out of `"chat":{"id": ...}` in the JSON response.
4. Put both values in `.env` (see below).

### 3. Email via SMTP (e.g. Gmail App Password)

1. Enable 2-Step Verification on your Google account, then create an [App
   Password](https://myaccount.google.com/apppasswords).
2. Set `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER` to your Gmail
   address, `SMTP_PASS` to the app password, `EMAIL_TO` to where you want
   alerts.

### 4. Configure

```bash
cp config.example.yaml config.yaml   # edit target_dates, time_window, etc.
cp .env.example .env                  # fill in secrets, then: set -a; source .env; set +a
```

Secrets are **only** read from environment variables -- never put a token or
password in `config.yaml`.

### 5. Test end-to-end

```bash
python monitor.py --once --dry-run -v
```

This logs what it *would* send without actually contacting Telegram/SMTP.
Once you're happy, drop `--dry-run` to send a real test alert (works
immediately if your target date is already within the current booking
window -- e.g. pick a date ~5 weeks out for a first real-world check).

## Config reference (`config.yaml`)

| Key | Meaning |
|---|---|
| `target_dates` | List of dates to watch. Each entry is either a plain `"YYYY-MM-DD"` string (uses the default `time_window` below), or a mapping `{date: "YYYY-MM-DD", time_window: {start, end}}` for a per-date override -- see the example below |
| `time_window.start` / `end` | Default `HH:MM` filter (shop-local time) for any `target_dates` entry that doesn't set its own override; omit for any time |
| `timezone` | Shop timezone, default `America/Los_Angeles` |
| `acuity.owner` / `appointment_type_id` / `calendar_id` | Booking-page identifiers, already filled in for E sharp hair |
| `booking_window_months_estimate` | Initial guess only; monitor self-corrects once it observes reality |
| `poll.far_hours` / `near_minutes` / `hot_minutes` / `found_hours` / `min_minutes` | Adaptive polling cadence (minutes/hours); `min_minutes` is a hard floor |
| `channels.telegram` / `channels.email` | Enable/disable each channel independently |
| `alert_on_removal` | Also alert when a previously-seen slot disappears |
| `heartbeat` | Send a daily "still watching" message |
| `alert_rate_limit_seconds` | Max 1 alert per channel per this many seconds (default 600 = 10 min) |
| `dry_run` | Log instead of sending (also settable via `--dry-run`) |
| `state_file` / `log_file` | Paths for persisted state / rotating log |

Mixed per-date time-window example:

```yaml
target_dates:
  - "2026-08-28"          # any time (falls back to the default time_window)
  - date: "2026-09-18"     # only this date's own window applies
    time_window:
      start: "17:00"
      end: "18:00"

time_window: {}            # default for entries with no override -- empty = any time
```

Note the dashboard (mode 3 below) doesn't expose per-date windows in the UI
yet -- dates added there always use the global default.

## Deployment mode 1: long-running daemon

Best for a Raspberry Pi, home server, or cheap VPS -- most precise timing for
the 5-minute "hot" window.

```bash
python monitor.py --config config.yaml
```

Runs forever with the adaptive scheduler described above, saving
`state.json` after every poll, until `SIGINT`/`SIGTERM`.

A `systemd` unit is provided at `systemd/haircut-alarm.service` -- edit the
`User`, `WorkingDirectory`, and `EnvironmentFile` paths, then:

```bash
sudo cp systemd/haircut-alarm.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now haircut-alarm
sudo journalctl -u haircut-alarm -f   # tail logs
```

## Deployment mode 2: stateless cron / GitHub Actions

Each invocation does a single check and exits; state persists in
`state.json`, which the GitHub Actions workflow commits back to the repo.

```bash
python monitor.py --once --config config.yaml
```

`.github/workflows/monitor.yml` runs this on a cron (`*/15 * * * *` by
default -- GitHub Actions cron granularity is >=5 min and can be delayed
under load, so it's *not* precise enough for the 5-minute hot window; use
daemon mode locally if you need that precision, or treat GH Actions as the
far/near-phase backstop). Set these as repository secrets: `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`,
`EMAIL_TO`. The workflow needs `contents: write` permission (already set) to
commit `state.json` back.

## Deployment mode 3: web dashboard

A visual dashboard for entering the list of target dates and watching their
status live, instead of hand-editing `config.yaml`.

```bash
pip install -r requirements-dashboard.txt
python dashboard.py                 # http://127.0.0.1:5000
```

One process does both jobs: a background thread runs the same adaptive
polling loop as `monitor.py`'s daemon mode (far/near/hot/found cadence,
Telegram/email alerts -- nothing about notifications changes), while Flask
serves the UI at `/`. It's fine to start with **zero** dates in
`config.yaml` (`target_dates: []` or the key omitted entirely) and add them
all from the browser.

What you can do from the page:
- **Add target date(s)** -- a native date picker for one at a time (click "+
  Add date" repeatedly to build a list), or paste several at once into the
  text box (comma- or newline-separated) and hit "Add all". Each date shows
  inline validation errors (bad format, date in the past).
- **Watch live status per date** -- a badge (Watching / Window open, no slot
  / **Slot available!** / Polling failing / Passed / Dismissed), days until
  the target, the estimated vs. measured booking-window open date, and the
  actual open times with a direct "Book now" link the moment one appears.
  The page polls `/api/status` every 15s; there's also a manual **Poll now**
  button.
- **Dismiss / re-enable / remove** a date -- dismiss stops alerts without
  losing history (e.g. you're still deciding), remove deletes it entirely
  (e.g. you already booked).
- **Send test notification** -- fires a real (or dry-run, per `dry_run` in
  config) message through both configured channels so you can confirm
  Telegram/email are wired up correctly before you need them for real.

Added/removed dates are written straight into `state.json` (not
`config.yaml`), and the background poller picks them up immediately --
adding a date wakes it early instead of waiting for the next scheduled poll.

Binds to `127.0.0.1` by default, no auth needed for pure-local use. If you
pass `--host` anything other than `127.0.0.1`/`localhost` (exposing it beyond
your own machine), the dashboard **requires** `DASHBOARD_PASSWORD` to be set
and refuses to start otherwise -- see "Security" below. A `systemd` unit is
provided at `systemd/haircut-dashboard.service` (edit
`User`/`WorkingDirectory`/`EnvironmentFile`, then
`systemctl enable --now haircut-dashboard`) -- don't run it alongside
`haircut-alarm.service`, they'd poll independently and double up on
requests/alerts.

### Security

The dashboard's `/api/targets`, `/api/poll-now`, and `/api/test-notification`
endpoints mutate state and send real notifications, so they need protecting
the moment the dashboard is reachable by anyone other than you:

- **Loopback-only (`127.0.0.1`, the default)**: no auth needed, nothing but
  your own machine can reach it.
- **Any other `--host`** (LAN, `0.0.0.0`, a public deployment like Fly.io):
  set `DASHBOARD_USER` (default `admin`) and `DASHBOARD_PASSWORD` as
  environment variables. All routes then require HTTP Basic Auth, checked
  with a constant-time comparison. `dashboard.py` refuses to bind to a
  non-loopback host at all if `DASHBOARD_PASSWORD` isn't set -- this is
  enforced in code, not just documented, so you can't accidentally expose it
  unauthenticated.
- This is HTTP Basic Auth over plain HTTP unless you're behind TLS -- Fly.io
  terminates TLS for you (`force_https = true` in `fly.toml`), so credentials
  aren't sent in the clear when deployed there. If you self-host behind
  something else, put TLS in front of it too (Caddy/nginx/Fly/Cloudflare
  Tunnel) before treating Basic Auth as sufficient.

## Deployment mode 4: Fly.io (public URL, always-on)

Runs the exact same single-process dashboard (Flask + background poller
thread) as mode 3, just containerized and hosted on Fly.io so you get a
public `https://<your-app>.fly.dev` URL without keeping your own machine on.

> Fly.io's free/low-cost allowances and pricing change over time -- verify
> current terms at [fly.io/docs/about/pricing](https://fly.io/docs/about/pricing)
> before deploying. The config below sets `min_machines_running = 1` and
> `auto_stop_machines = false` deliberately: Fly's default "scale to zero
> when idle" behavior would silently stop the background polling thread,
> since it doesn't run in response to HTTP requests.

1. **Install flyctl and log in**: see [fly.io/docs/flyctl/install](https://fly.io/docs/flyctl/install/),
   then `fly auth login`.

2. **Create your config.yaml** (this repo's `config.yaml` is gitignored but
   *is* picked up by `docker build`'s local build context -- it has no
   secrets, so it's fine to bake into the image):
   ```bash
   cp config.example.yaml config.yaml
   ```
   Edit it: set `state_file: "/data/state.json"` and `log_file: "/data/monitor.log"`
   (the persistent volume mounted at `/data`), and leave `target_dates: []`
   if you'll add dates from the dashboard UI instead.

3. **Edit `fly.toml`**: change `app = "CHANGE-ME-haircut-dashboard"` to a
   globally-unique name, and `primary_region` to whatever
   `fly platform regions` shows as closest to you.

4. **Create the app and its persistent volume** (the volume is what keeps
   `state.json` -- your watched dates and alert history -- across deploys and
   restarts; without it every deploy would start from empty state):
   ```bash
   fly apps create <your-app-name>
   fly volumes create data --size 1 --region <your-region>
   ```

5. **Set secrets** (these become environment variables inside the container,
   read the same way as `.env` locally). `DASHBOARD_PASSWORD` is mandatory
   here since the app will be on a public bind (`0.0.0.0`, per the Dockerfile):
   ```bash
   fly secrets set \
     DASHBOARD_PASSWORD='choose-a-real-password' \
     TELEGRAM_BOT_TOKEN='...' TELEGRAM_CHAT_ID='...' \
     SMTP_HOST='smtp.gmail.com' SMTP_PORT='587' SMTP_USER='...' SMTP_PASS='...' EMAIL_TO='...'
   ```

6. **Deploy**:
   ```bash
   fly deploy
   fly open   # or visit https://<your-app-name>.fly.dev
   ```
   Log in with `DASHBOARD_USER` (default `admin`) and the password you set.

7. **Watch logs / redeploy after config changes**:
   ```bash
   fly logs
   # after editing config.yaml or code:
   fly deploy
   ```

This sandboxed development session could not actually run `docker build` or
`fly deploy` (no privileged container support here) -- the Dockerfile/fly.toml
were validated by running the exact command the container uses
(`python dashboard.py --config config.yaml --host 0.0.0.0 --port 8080`)
directly against a local `/data` directory and confirming it binds correctly,
enforces auth, and writes `state.json`/`monitor.log` to the expected paths.
Do a real `fly deploy` yourself and check `fly logs` before relying on it.

## Fallback: browser engine

If sustained polling via plain `requests` gets blocked (bot detection,
Cloudflare challenge), install the browser extra and pass `--engine=browser`:

```bash
pip install -r requirements-browser.txt
playwright install chromium
python monitor.py --once --engine=browser
```

`browser_engine.py` drives the real booking page in headless Chromium and
reads the same two JSON endpoints from a real browser session -- same data,
just harder to fingerprint as a bot.

## Managing alerts

- `python monitor.py --dismiss 2026-09-26` stops all future alerts for that
  date (e.g. once you've booked) without touching the others.
- Delete/edit `state.json` to reset dedup state for a date if you want to be
  re-alerted for an already-seen slot set.

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

Covers: initial-estimate date math (month-end overflow, leap years,
fractional months), false→true flip detection, furthest-bookable-date
tracking, slot-set diffing/dedup, alert rate-limiting, time-window
filtering, adaptive interval phase selection, config loading, the Acuity
client's retry/backoff and schema-validation behavior, and the dashboard's
Flask API (add/remove/dismiss dates, HTTP Basic Auth enforcement) via Flask's
test client -- all mocked, no network access needed. `tests/fixtures/` holds
sample `/month` and `/times` responses matching the documented shapes.

## Troubleshooting

- **"schema changed, needs update" / `AcuitySchemaError` in logs**: Acuity
  changed their response shape, or you're getting an HTML bot-detection page
  back with a 200. Check the logged raw body snippet, update
  `acuity_client.py`'s validation to match, and consider `--engine=browser`.
- **Repeated `HTTP 403`/`429` in logs**: you're likely being rate-limited or
  bot-detected. The client already backs off with jitter on retryable
  statuses (429/5xx); a hard 403 is treated as non-retryable within a single
  call but will keep being retried on the next poll cycle. If persistent,
  switch to `--engine=browser`.
- **No alerts despite an open slot**: check `channels.telegram`/`channels.email`
  are `true` in `config.yaml`, the relevant env vars are set, `dry_run` is
  `false`, and `state.json`'s `last_alert_ts` for that date isn't inside the
  `alert_rate_limit_seconds` window.
- **3 consecutive polling failures**: you'll get a single WARNING alert (not
  spammed every poll) so you know the monitor itself needs attention;
  `consecutive_failures` resets to 0 on the next successful poll.
- **GitHub Actions workflow not committing `state.json`**: confirm the
  workflow has `permissions: contents: write` (set in
  `.github/workflows/monitor.yml`) and that the default `GITHUB_TOKEN` hasn't
  been restricted to read-only at the repo/org level.
- **`dashboard.py` refuses to start with "Refusing to bind to non-loopback
  host..."**: expected -- set `DASHBOARD_PASSWORD` (see Security above). This
  is a hard requirement, not a warning, once `--host` isn't `127.0.0.1`.
- **Fly.io deploy loses watched dates after a redeploy**: `state.json` isn't
  on the persistent volume. Confirm `config.yaml`'s `state_file` is
  `/data/state.json` (matching `fly.toml`'s `[mounts] destination = "/data"`)
  and that you ran `fly volumes create data ...` before the first deploy.
- **Fly.io app seems to stop polling after a while**: check `fly status` --
  if the machine shows as stopped, `min_machines_running = 1` and
  `auto_stop_machines = false` may have been reverted (e.g. by `fly launch`
  re-running and regenerating `fly.toml`). Re-apply those two settings and
  `fly deploy` again.
