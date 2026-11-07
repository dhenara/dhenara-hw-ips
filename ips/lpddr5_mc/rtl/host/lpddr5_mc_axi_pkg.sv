`timescale 1ns/1ps
package lpddr5_mc_axi_pkg;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_host_pkg::*;
  typedef struct packed {
    lpddr5_mc_host_address_t address;
    logic write_access;
    logic [63:0] footprint;
    logic [31:0] boot_epoch, config_epoch;
  } lpddr5_mc_axi_proposal_t;
  typedef struct packed {
    logic revoke;
    logic [63:0] mask;
    logic [31:0] token, boot_epoch, config_epoch;
  } lpddr5_mc_axi_lease_command_t;
  typedef struct packed {
    lpddr5_mc_axi_lease_command_t command;
    logic [31:0] accepted_next_sequence;
  } lpddr5_mc_axi_lease_ack_t;

  // Every valid AXI burst is contained in a page; admitted region boundaries
  // are page aligned. Whole-burst containment is therefore bounded by the
  // eight regions rather than by the number of beats or active byte lanes.
  function automatic logic burst_mapped(
      input lpddr5_mc_axi_address_t a, input lpddr5_mc_host_region_t [7:0] regions,
      input int data_bits, id_bits, address_bits);
    logic [40:0] first, limit, span;
    logic mapped;
    first = {1'b0,a.address};
    limit = beat_limit(a,{1'b0,a.len});
    if (a.burst == 2) begin
      span = (41'(a.len)+1) << a.size;
      first &= ~(span-1);
      limit = first+span;
    end
    mapped = 0;
    for (int i=0;i<8;i++)
      mapped |= regions[i].enable && first >= 41'(regions[i].base)
        && limit <= 41'(regions[i].base)+regions[i].size;
    return address_form_valid(a,data_bits,id_bits,address_bits) && mapped;
  endfunction

  // Ownership uses logical 64-byte buckets. The admitted map is injective
  // and preserves the low six bits, so disjoint buckets prove disjoint memory
  // effects under every allowed permutation. Hash collisions only serialize.
  function automatic logic [63:0] burst_footprint(
      input lpddr5_mc_axi_address_t a, input logic mapped);
    logic [40:0] first, limit, span;
    logic [63:0] result;
    first = {1'b0,a.address};
    limit = beat_limit(a,{1'b0,a.len});
    if (a.burst == 2) begin
      span = (41'(a.len)+1) << a.size;
      first &= ~(span-1);
      limit = first+span;
    end
    result = '0;
    for (int i=0;i<64;i++)
      if (41'(i) >= ((first >> 6) & 41'd63)
          && 41'(i) <= (((limit-1) >> 6) & 41'd63)) result[i] = 1;
    // Invalid/unmapped traffic takes ordinary all-bucket ownership. It never
    // bypasses ordering or recovery through an empty hazard footprint.
    return mapped ? result : 64'hffffffffffffffff;
  endfunction

  typedef struct packed {
    lpddr5_mc_axi_b_t axi;
    logic [5:0] slot;
    logic [15:0] generation;
  } lpddr5_mc_axi_b_packet_t;
  typedef struct packed {
    lpddr5_mc_axi_read_t axi;
    logic [5:0] slot;
    logic [15:0] generation;
  } lpddr5_mc_axi_r_packet_t;
endpackage
