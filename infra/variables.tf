variable "compartment_ocid" {
  description = "OCID of the compartment to create all resources in."
  type        = string
}

variable "region" {
  description = "OCI region to deploy into, e.g. \"us-ashburn-1\". Must be a region where your tenancy has Always Free capacity."
  type        = string
}

variable "availability_domain_index" {
  description = "Zero-based index into the compartment's list of availability domains. Change this and re-apply if you hit an out-of-capacity error on the Always Free shape in your first-choice AD."
  type        = number
  default     = 0
}

variable "cidr_ssh_allowed" {
  description = "CIDR allowed to reach port 22, e.g. \"203.0.113.4/32\" for a single IP. Never leave this as 0.0.0.0/0 -- this is the only inbound rule that exists at all."
  type        = string

  validation {
    condition     = var.cidr_ssh_allowed != "0.0.0.0/0"
    error_message = "cidr_ssh_allowed must not be 0.0.0.0/0 -- restrict SSH to a specific IP or range you control."
  }
}

variable "ssh_public_key" {
  description = "Contents of the SSH public key (not a path) for the deploy user, e.g. file(\"~/.ssh/id_ed25519.pub\")."
  type        = string
}

variable "deploy_user" {
  description = "Non-root username cloud-init creates for SSH/deploy access."
  type        = string
  default     = "deploy"
}

variable "name_prefix" {
  description = "Prefix applied to the display name of every resource this stack creates, so it's identifiable in the OCI console."
  type        = string
  default     = "acuity-alarm"
}

variable "vcn_cidr" {
  description = "CIDR block for the VCN."
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR block for the single public subnet."
  type        = string
  default     = "10.0.1.0/24"
}
