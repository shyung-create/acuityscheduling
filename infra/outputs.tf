output "instance_ocid" {
  description = "OCID of the compute instance."
  value       = oci_core_instance.app.id
}

output "reserved_public_ip" {
  description = "Reserved public IP address. Stable across instance recreation -- use this for SSH, not the instance's ephemeral address."
  value       = oci_core_public_ip.reserved.ip_address
}

output "ssh_command" {
  description = "Convenience SSH command using the reserved public IP and the deploy user cloud-init creates."
  value       = "ssh ${var.deploy_user}@${oci_core_public_ip.reserved.ip_address}"
}

output "vcn_id" {
  value = oci_core_vcn.this.id
}

output "subnet_id" {
  value = oci_core_subnet.public.id
}
