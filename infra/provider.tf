# Credentials are intentionally NOT variables here -- the provider reads
# them from the standard OCI CLI config file (~/.oci/config, set up via
# `oci setup config`) or from the OCI_CLI_* / TF_VAR_* environment variables
# OCI's provider supports natively. Same "no credentials in code" rule the
# rest of this repo follows for Telegram/Acuity secrets.
provider "oci" {
  region = var.region
}
