# TEMPORARY -- for diagnosing the image filter in compute.tf. Delete this
# file once compute.tf's data "oci_core_images" "ubuntu_minimal" is fixed
# and matching real images again.

data "oci_core_images" "debug_ubuntu_all" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "22.04"
  shape                    = "VM.Standard.E2.1.Micro"
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

output "debug_ubuntu_image_names" {
  value = [for img in data.oci_core_images.debug_ubuntu_all.images : img.display_name]
}
