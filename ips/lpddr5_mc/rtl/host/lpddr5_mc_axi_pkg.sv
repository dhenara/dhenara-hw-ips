`timescale 1ns/1ps
package lpddr5_mc_axi_pkg;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_host_pkg::*;
  typedef struct packed {
    lpddr5_mc_host_address_t address;
    logic write_access;
  } lpddr5_mc_axi_proposal_t;
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
