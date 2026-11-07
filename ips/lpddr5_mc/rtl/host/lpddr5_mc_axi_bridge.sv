`timescale 1ns/1ps
// A single acknowledged grant globally linearizes actual asynchronous address
// acceptance. FIFO arrival time never defines transaction order.
module lpddr5_mc_axi_bridge #(
  parameter int PORTS = 2,
  parameter int CHANNELS = 2,
  parameter int RANKS = 1,
  parameter int DATA_BITS = 128,
  parameter int ID_BITS = 8,
  parameter int ADDRESS_BITS = 40,
  parameter int DEPTH = 64
) (
  input logic core_clk_i, core_rst_ni, enable_i, fatal_i, core_settled_i,
  input logic rearm_i,
  output logic rearm_ready_o, initialized_o, fault_o, host_settled_o, abort_settled_o, admission_busy_o,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_config_t cfg_i,
  input logic cfg_valid_i,
  input logic [PORTS-1:0] axi_clk_i, axi_rst_ni,
  input logic [PORTS-1:0] awvalid_i, arvalid_i, wvalid_i,
  output logic [PORTS-1:0] awready_o, arready_o, wready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_address_t [PORTS-1:0] aw_i, ar_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_write_t [PORTS-1:0] w_i,
  output logic [PORTS-1:0] bvalid_o, rvalid_o,
  input logic [PORTS-1:0] bready_i, rready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_b_t [PORTS-1:0] b_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_read_t [PORTS-1:0] r_o,
  output logic [PORTS-1:0] late_error_o,
  input logic [PORTS-1:0][1:0][DEPTH-1:0] core_slot_available_i,
  output logic [PORTS-1:0] core_awvalid_o, core_arvalid_o, core_wvalid_o,
  input logic [PORTS-1:0] core_awready_i, core_arready_i, core_wready_i,
  output lpddr5_mc_host_pkg::lpddr5_mc_host_address_t [PORTS-1:0] core_aw_o, core_ar_o,
  output lpddr5_mc_host_pkg::lpddr5_mc_host_write_t [PORTS-1:0] core_w_o,
  input logic [PORTS-1:0] core_bvalid_i, core_rvalid_i,
  output logic [PORTS-1:0] core_bready_o, core_rready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_b_t [PORTS-1:0] core_b_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_read_t [PORTS-1:0] core_r_i,
  input logic [PORTS-1:0][5:0] core_bslot_i, core_rslot_i,
  input logic [PORTS-1:0][15:0] core_bgeneration_i, core_rgeneration_i,
  input logic [PORTS-1:0] core_retire_valid_i,
  output logic [PORTS-1:0] core_retire_ready_o,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_response_event_t [PORTS-1:0] core_retire_i,
  output logic [PORTS-1:0] core_event_valid_o,
  input logic [PORTS-1:0] core_event_ready_i,
  output lpddr5_mc_host_pkg::lpddr5_mc_host_response_event_t [PORTS-1:0] core_event_o,
  output logic [63:0] accepted_addresses_o, grant_cycles_o
);
  import lpddr5_mc_host_pkg::*;
  import lpddr5_mc_axi_pkg::*;
  logic core_reset_n, link_reset_n, link_arst_n, rearm;
  logic [PORTS-1:0] endpoint_initialized, endpoint_fault, endpoint_settled, endpoint_admission_idle;
  logic [PORTS-1:0] endpoint_abort_settled;
  logic [PORTS-1:0] endpoint_transport_reset_ready;
  logic [PORTS-1:0] endpoint_quiesce_ack;
  logic quiesce_request_q, quiesce_issued_q, enable_seen_q;
  logic [PORTS-1:0] proposal_valid, proposal_ready, grant_valid, grant_ready, accepted_valid, accepted_ready;
  logic [PORTS-1:0] endpoint_bready, endpoint_rready, endpoint_retire_ready;
  lpddr5_mc_axi_proposal_t [PORTS-1:0] proposal, accepted;
  lpddr5_mc_axi_b_packet_t [PORTS-1:0] b_packets;
  lpddr5_mc_axi_r_packet_t [PORTS-1:0] r_packets;
  typedef enum logic [1:0] {IDLE, GRANT, ACCEPT} state_t;
  state_t state_q;
  localparam int PORT_BITS = PORTS > 1 ? $clog2(PORTS) : 1;
  logic [PORT_BITS-1:0] selected_q;
  int selected;
  lpddr5_mc_axi_proposal_t grant_q;
  logic was_active_q, rearming_q, protocol_fault_q;
  lpddr5_mc_reset_sync core_reset (
    .clk_i(core_clk_i),.arst_ni(core_rst_ni),.rst_no(core_reset_n)
  );
  assign link_arst_n = core_reset_n && (&axi_rst_ni) && !fatal_i;
  lpddr5_mc_reset_sync link_reset (
    .clk_i(core_clk_i),.arst_ni(link_arst_n),.rst_no(link_reset_n)
  );
  assign initialized_o = &endpoint_initialized;
  assign fault_o = was_active_q && ((|endpoint_fault) || protocol_fault_q || !( &axi_rst_ni));
  assign abort_settled_o = (&endpoint_abort_settled) && state_q == IDLE;
  assign host_settled_o = (&endpoint_settled) && state_q == IDLE
    && (!(fatal_i || fault_o) || abort_settled_o);
  // The enable fall itself closes the stale-idle gap before the first core
  // edge can publish the new request toggle. Never use a fixed settling delay.
  assign admission_busy_o = enable_i || (enable_seen_q && !enable_i) || !quiesce_issued_q
    || state_q != IDLE || !(&endpoint_admission_idle)
    || endpoint_quiesce_ack != {PORTS{quiesce_request_q}};
  assign rearm_ready_o = link_reset_n && !enable_i && cfg_valid_i && core_settled_i
    && (&endpoint_transport_reset_ready) && host_settled_o && !rearming_q;
  assign rearm = rearm_i && rearm_ready_o;
  always_ff @(posedge core_clk_i or negedge core_reset_n) begin
    if (!core_reset_n) begin
      was_active_q <= 0; quiesce_request_q <= 0; quiesce_issued_q <= 0; enable_seen_q <= 0;
    end else begin
      if (initialized_o) was_active_q <= 1;
      enable_seen_q <= enable_i;
      if (enable_i) quiesce_issued_q <= 0;
      else if (!quiesce_issued_q) begin
        quiesce_request_q <= ~quiesce_request_q; quiesce_issued_q <= 1;
      end
    end
  end
  always_ff @(posedge core_clk_i or negedge link_reset_n) begin
    if (!link_reset_n) begin
      state_q <= IDLE; selected_q <= 0; grant_q <= '0;
      rearming_q <= 0; protocol_fault_q <= 0; accepted_addresses_o <= 0; grant_cycles_o <= 0;
    end else begin
      if (rearm) begin rearming_q <= 1; protocol_fault_q <= 0; end
      else if (rearming_q && initialized_o) rearming_q <= 0;
      if (state_q != IDLE) grant_cycles_o <= grant_cycles_o+1;
      if (state_q == IDLE && selected >= 0) begin
        grant_q <= proposal[selected]; selected_q <= PORT_BITS'(selected); state_q <= GRANT;
      end
      if (state_q == GRANT && grant_ready[selected_q]) state_q <= ACCEPT;
      if (state_q == ACCEPT && accepted_valid[selected_q]) begin
        if (accepted[selected_q] != grant_q) protocol_fault_q <= 1;
        else if (accepted_ready[selected_q]) begin
          state_q <= IDLE;
          accepted_addresses_o <= accepted_addresses_o+1;
        end
      end
    end
  end
  always_comb begin
    selected = -1;
    proposal_ready = 0; grant_valid = 0; accepted_ready = 0;
    core_awvalid_o = 0; core_arvalid_o = 0;
    for (int p=0;p<PORTS;p++) begin
      core_aw_o[p] = accepted[p].address;
      core_ar_o[p] = accepted[p].address;
      // A proposal already published must finish even while admission quiesces.
      // Sources stop creating candidates after their synchronized enable clears.
      if (selected < 0 && initialized_o && !fault_o && proposal_valid[p]
          && int'(proposal[p].address.slot)<DEPTH
          && core_slot_available_i[p][proposal[p].write_access ? 0 : 1][$clog2(DEPTH)'(proposal[p].address.slot)]) selected = p;
    end
    if (state_q == GRANT && !fault_o) begin
      grant_valid[selected_q] = 1;
      proposal_ready[selected_q] = grant_ready[selected_q];
    end
    if (state_q == ACCEPT && accepted_valid[selected_q] && accepted[selected_q] == grant_q && !fault_o) begin
      if (grant_q.write_access) begin
        core_awvalid_o[selected_q] = 1;
        accepted_ready[selected_q] = core_awready_i[selected_q];
      end else begin
        core_arvalid_o[selected_q] = 1;
        accepted_ready[selected_q] = core_arready_i[selected_q];
      end
    end
    // Internal response acceptance is transport movement, never host exposure.
    // Fatal settlement must also drain the old core's held output registers.
    core_bready_o = fatal_i ? '1 : endpoint_bready;
    core_rready_o = fatal_i ? '1 : endpoint_rready;
    core_retire_ready_o = fatal_i ? '1 : endpoint_retire_ready;
    for (int p=0;p<PORTS;p++) begin
      b_packets[p].axi = core_b_i[p]; b_packets[p].slot = core_bslot_i[p]; b_packets[p].generation = core_bgeneration_i[p];
      r_packets[p].axi = core_r_i[p]; r_packets[p].slot = core_rslot_i[p]; r_packets[p].generation = core_rgeneration_i[p];
    end
  end
  for (genvar p=0;p<PORTS;p++) begin : ports
    lpddr5_mc_axi_endpoint #(.PORT_INDEX(p),.CHANNELS(CHANNELS),.RANKS(RANKS),
      .DATA_BITS(DATA_BITS),.ID_BITS(ID_BITS),.ADDRESS_BITS(ADDRESS_BITS),.DEPTH(DEPTH)) endpoint (
      .axi_clk_i(axi_clk_i[p]),.axi_rst_ni(axi_rst_ni[p]),.core_clk_i,.core_rst_ni(core_reset_n),.core_link_rst_ni(link_reset_n),
      .core_enable_i(enable_i),.core_rearm_i(rearm),.core_cfg_i(cfg_i),
      .core_quiesce_request_i(quiesce_request_q),.core_quiesce_ack_o(endpoint_quiesce_ack[p]),
      .core_transport_reset_ready_o(endpoint_transport_reset_ready[p]),
      .core_initialized_o(endpoint_initialized[p]),.core_fault_o(endpoint_fault[p]),.core_settled_o(endpoint_settled[p]),
      .core_abort_settled_o(endpoint_abort_settled[p]),
      .core_admission_idle_o(endpoint_admission_idle[p]),
      .late_error_o(late_error_o[p]),.awvalid_i(awvalid_i[p]),.arvalid_i(arvalid_i[p]),.wvalid_i(wvalid_i[p]),
      .awready_o(awready_o[p]),.arready_o(arready_o[p]),.wready_o(wready_o[p]),.aw_i(aw_i[p]),.ar_i(ar_i[p]),.w_i(w_i[p]),
      .bvalid_o(bvalid_o[p]),.rvalid_o(rvalid_o[p]),.bready_i(bready_i[p]),.rready_i(rready_i[p]),.b_o(b_o[p]),.r_o(r_o[p]),
      .proposal_valid_o(proposal_valid[p]),.proposal_ready_i(proposal_ready[p]),.proposal_o(proposal[p]),
      .grant_valid_i(grant_valid[p]),.grant_ready_o(grant_ready[p]),.grant_i(grant_q),
      .accepted_valid_o(accepted_valid[p]),.accepted_ready_i(accepted_ready[p]),.accepted_o(accepted[p]),
      .core_wvalid_o(core_wvalid_o[p]),.core_wready_i(core_wready_i[p]),.core_w_o(core_w_o[p]),
      .core_bvalid_i(core_bvalid_i[p] && !fatal_i),.core_rvalid_i(core_rvalid_i[p] && !fatal_i),
      .core_bready_o(endpoint_bready[p]),.core_rready_o(endpoint_rready[p]),.core_b_i(b_packets[p]),.core_r_i(r_packets[p]),
      .core_retire_valid_i(core_retire_valid_i[p] && !fatal_i),.core_retire_ready_o(endpoint_retire_ready[p]),
      .core_retire_i(core_retire_i[p]),.core_event_valid_o(core_event_valid_o[p]),
      .core_event_ready_i(core_event_ready_i[p]),.core_event_o(core_event_o[p])
    );
  end
endmodule
