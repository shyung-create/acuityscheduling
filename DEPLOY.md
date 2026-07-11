# Continuous deploy (GitHub Actions -> OCI instance)

`.github/workflows/deploy.yml` runs on every push to `main`: rsync the repo
to `/opt/acuity-alarm`, recreate the venv only if
`requirements-appointment-alarm.txt` changed, then
`systemctl restart acuity-alarm.timer`. No build step, no container image --
`main.py` is a plain interpreted script.

This assumes the server already exists (`infra/`) and has already gone
through the one-time setup in `SETUP.md` (dedicated `acuityalarm` user,
systemd units installed and enabled, `deploy` user from cloud-init present).
This workflow does not create any of that, and it does not touch
`/etc/systemd/system/*` -- if you change `systemd/acuity-alarm.service` or
`.timer`, redeploy them manually (see `SETUP.md`) and
`systemctl daemon-reload`; the CI job only restarts the already-installed
timer.

## First-time setup

### 1. Generate a deploy key and authorize it on the server

```bash
ssh-keygen -t ed25519 -f ./acuity-alarm-deploy-key -C "acuity-alarm-deploy" -N ""
```

Append the **public** half to the `deploy` user's `authorized_keys` on the
server (the `deploy` user already exists from cloud-init):

```bash
ssh-copy-id -i ./acuity-alarm-deploy-key.pub deploy@<reserved-ip>
# or manually: cat acuity-alarm-deploy-key.pub | ssh deploy@<reserved-ip> "cat >> ~/.ssh/authorized_keys"
```

### 2. Add the GitHub secret and repo variable

In the repo's Settings -> Secrets and variables -> Actions:

- **Secret** `DEPLOY_SSH_PRIVATE_KEY` -- the contents of the **private** key
  (`acuity-alarm-deploy-key`, no passphrase, since CI can't type one).
- **Variable** `OCI_RESERVED_PUBLIC_IP` -- the reserved public IP from
  `terraform output reserved_public_ip` in `infra/`. Not a secret; it's an
  address, not a credential.

Delete the local private key file once it's in GitHub Secrets:

```bash
rm ./acuity-alarm-deploy-key ./acuity-alarm-deploy-key.pub
```

### 3. Put `.env` on the server -- manually, never through CI

The pipeline never sees Telegram/Acuity credentials -- it only rsyncs code
(`.env` is explicitly excluded from the sync). Copy it once, directly, over
your own SSH session:

```bash
scp .env deploy@<reserved-ip>:/tmp/.env
ssh deploy@<reserved-ip>
sudo mv /tmp/.env /opt/acuity-alarm/.env
sudo chown acuityalarm:acuityalarm /opt/acuity-alarm/.env
sudo chmod 600 /opt/acuity-alarm/.env
```

Update it in place (same `scp`/`mv`/`chmod` steps) whenever credentials
change -- CI will never overwrite or touch this file.

### 4. First deploy

Push to `main`, or run the workflow manually (Actions -> Deploy appointment
alarm -> Run workflow). The first run will find no
`requirements-appointment-alarm.txt` hash on the server, so it creates the
venv from scratch.

## Operating the deployed service

Tail logs as cycles run:

```bash
ssh deploy@<reserved-ip>
journalctl -u acuity-alarm.service -f
```

Trigger one poll cycle right now, without waiting for the timer (useful
after a deploy, or to test a config change):

```bash
sudo systemctl start acuity-alarm.service
```

Check the timer's schedule and last/next run:

```bash
systemctl status acuity-alarm.timer
systemctl list-timers acuity-alarm.timer
```

## What this pipeline deliberately does not do

- Doesn't manage secrets (`.env` is server-side only, set up manually).
- Doesn't install or update systemd unit files, or run `daemon-reload` --
  those are a manual `SETUP.md` step, since they change rarely and a bad
  unit file shouldn't be one `git push` away from landing unreviewed.
- Doesn't roll back automatically on failure -- if `systemctl restart`
  fails, the workflow run goes red and you fix forward (check
  `journalctl -u acuity-alarm.service` on the server, or watch the failed
  step's SSH output in the Actions log).
