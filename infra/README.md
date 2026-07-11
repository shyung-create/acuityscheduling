# Infrastructure (OCI, Terraform)

Provisions just enough to run the appointment alarm's `main.py` on a
schedule: a network with no inbound listener except SSH, and one compute
instance. Deploying the app itself onto this VM (systemd units, `.env`,
venv) is a separate step -- see the repo root's `SETUP.md`.

## ⚠️ This intentionally uses the AMD Always Free shape, not Ampere A1

`compute.tf` provisions `VM.Standard.E2.1.Micro` (AMD, 1/8 OCPU, 1GB RAM).
**Do not "upgrade" this to an Ampere A1 shape** (`VM.Standard.A1.Flex`) for
more headroom -- it doesn't need it, and doing so would eat into the Ampere
A1 Always Free allowance for no reason.

Why this matters: OCI's Always Free tier has **two separate, independent
allowances**:

- **Ampere A1 (ARM, flex)** -- originally 4 OCPU / 24GB total, cut to
  **2 OCPU / 12GB in June 2026** with no formal Oracle announcement. This is
  the scarce, contested pool worth preserving for projects that actually
  need ARM or more resources.
- **AMD E2.1.Micro (fixed shape)** -- unaffected by that cut, allows up to 2
  such instances, and is exactly enough for a small poller like this one
  (no CPU/RAM adjustment needed or possible -- it's a fixed shape).

This workload -- polling an API every few minutes and posting to Telegram --
does not need Ampere's flexibility or extra capacity. Using E2.1.Micro here
keeps the full (reduced) A1 allowance free for something that actually needs
it.

## What this creates

- 1 VCN, 1 public subnet, 1 internet gateway, 1 route table
- 1 security list: inbound TCP/22 from `var.cidr_ssh_allowed` only,
  all outbound allowed. No other inbound ports -- this service never
  listens for incoming traffic, it only calls out to Acuity and Telegram
  over HTTPS.
- 1 `VM.Standard.E2.1.Micro` instance running Ubuntu 22.04, with
  cloud-init creating a `deploy` user (sudo, SSH-key auth only) and
  installing `python3`, `python3-venv`, `python3-pip`. No app code is
  deployed by cloud-init -- that's intentionally left to a separate step.
- 1 reserved (non-ephemeral) public IP attached to the instance, so its
  address survives instance recreation (shape/image changes, etc.) --
  useful since `cidr_ssh_allowed` and any DNS pointing at this box would
  otherwise need updating every time.

## Prerequisites

- Terraform >= 1.5
- An OCI CLI config file at `~/.oci/config` (`oci setup config`), or the
  equivalent `OCI_CLI_*` / `TF_VAR_*` environment variables. Credentials are
  never stored in this directory or in tfvars.
- An SSH key pair you control.

## Usage

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: compartment_ocid, region, cidr_ssh_allowed, ssh_public_key

terraform init
terraform plan
terraform apply
```

```bash
terraform output ssh_command
# ssh deploy@<reserved-ip>
```

If `terraform apply` fails with an out-of-host-capacity error for
`VM.Standard.E2.1.Micro`, that AD is temporarily out of Always Free capacity
-- try a different `availability_domain_index`, or retry later. Always Free
shapes are capacity-constrained per AD; this is expected and unrelated to
the Ampere A1 cut described above.

## Destroying

```bash
terraform destroy
```

The reserved public IP is destroyed along with everything else -- if you
recreate the stack later, expect a new IP and update
`cidr_ssh_allowed`/DNS/bookmarks accordingly.
