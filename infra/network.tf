# One VCN, one public subnet, an internet gateway, and a security list that
# allows inbound SSH only (from cidr_ssh_allowed) and all outbound. The
# polling side of this project never accepts incoming traffic -- it polls
# Acuity and posts to Telegram over outbound HTTPS -- so there is no
# port 443/80 rule and no load balancer.
#
# The one opt-in exception: if expose_dashboard_publicly = true,
# dashboard.py's web UI is opened on dashboard_port to 0.0.0.0/0. That's a
# real listener with only an application-level password (DASHBOARD_PASSWORD)
# protecting it, over plain HTTP (no domain here, so no easy path to a real
# TLS cert) -- an accepted tradeoff for a low-stakes personal dashboard, not
# the default posture of this stack.

resource "oci_core_vcn" "this" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "${var.name_prefix}-vcn"
  dns_label      = "acuityalarm"
}

resource "oci_core_internet_gateway" "this" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name_prefix}-igw"
  enabled        = true
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name_prefix}-public-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.this.id
  }
}

resource "oci_core_security_list" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.name_prefix}-public-sl"

  ingress_security_rules {
    protocol    = "6" # TCP
    source      = var.cidr_ssh_allowed
    source_type = "CIDR_BLOCK"
    description = "SSH, restricted to cidr_ssh_allowed -- the only inbound rule in this stack"

    tcp_options {
      min = 22
      max = 22
    }
  }

  dynamic "ingress_security_rules" {
    for_each = var.expose_dashboard_publicly ? [1] : []
    content {
      protocol    = "6" # TCP
      source      = "0.0.0.0/0"
      source_type = "CIDR_BLOCK"
      description = "Dashboard (dashboard.py) -- open to the internet, gated by DASHBOARD_PASSWORD at the application layer, not by source IP like SSH"

      tcp_options {
        min = var.dashboard_port
        max = var.dashboard_port
      }
    }
  }

  egress_security_rules {
    protocol         = "all"
    destination      = "0.0.0.0/0"
    destination_type = "CIDR_BLOCK"
    description      = "All outbound -- needed for apt, Acuity API, and Telegram Bot API calls"
  }
}

resource "oci_core_subnet" "public" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.this.id
  cidr_block                 = var.subnet_cidr
  display_name               = "${var.name_prefix}-public-subnet"
  dns_label                  = "public"
  route_table_id             = oci_core_route_table.public.id
  security_list_ids          = [oci_core_security_list.public.id]
  prohibit_public_ip_on_vnic = false
}
