# VM.Standard.E2.1.Micro: the AMD Always Free micro shape (1/8 OCPU, 1GB
# RAM). This draws from OCI's separate E2 Always Free allowance, not the
# Ampere A1 flex pool -- see README.md in this directory for why that
# distinction matters and must stay that way.

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.compartment_ocid
}

data "oci_core_images" "ubuntu_minimal" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "22.04"
  shape                    = "VM.Standard.E2.1.Micro"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"

  filter {
    name   = "display_name"
    values = ["^.*Minimal.*$"]
    regex  = true
  }
}

resource "oci_core_instance" "app" {
  compartment_id      = var.compartment_ocid
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[var.availability_domain_index].name
  display_name        = var.name_prefix
  shape               = "VM.Standard.E2.1.Micro"
  # Fixed (non-flex) shape -- no shape_config block. Unlike the Ampere A1
  # flex shape, its OCPU/RAM aren't adjustable, and that's fine: this
  # workload is a lightweight poller that doesn't need more.

  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    display_name     = "${var.name_prefix}-vnic"
    assign_public_ip = false # a RESERVED public IP is attached separately below so it survives recreation
  }

  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.ubuntu_minimal.images[0].id
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data = base64encode(templatefile("${path.module}/cloud-init.yaml.tftpl", {
      deploy_user    = var.deploy_user
      ssh_public_key = var.ssh_public_key
    }))
  }

  preserve_boot_volume = false
}

# The instance's VNIC, looked up post-creation so the reserved public IP
# below can attach to it. oci_core_vnic itself doesn't expose the private
# IP's OCID -- that requires the separate oci_core_private_ips lookup below,
# filtered by this VNIC's ID.
data "oci_core_vnic_attachments" "app" {
  compartment_id = var.compartment_ocid
  instance_id    = oci_core_instance.app.id
}

data "oci_core_private_ips" "app" {
  vnic_id = data.oci_core_vnic_attachments.app.vnic_attachments[0].vnic_id
}

# A RESERVED (not ephemeral) public IP is its own resource, independent of
# the instance's lifecycle -- if the instance is ever terminated and
# recreated (e.g. shape change, image update), this address is reattached
# rather than reassigned, so SSH access / firewall allowlists don't need to
# change.
resource "oci_core_public_ip" "reserved" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.name_prefix}-reserved-ip"
  lifetime       = "RESERVED"
  private_ip_id  = data.oci_core_private_ips.app.private_ips[0].id
}
