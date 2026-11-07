`timescale 1ns/1ps
// Per-source bucket leases permit local acceptance without an address round
// trip. Conflicting rights move only after exact revoke ACK/import watermarks
// and real response retirement; arrival order never overrides a hazard.
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
  logic [PORTS-1:0] proposal_valid, proposal_ready, lease_valid, lease_ready, accepted_valid, accepted_ready;
  logic [PORTS-1:0] lease_ack_valid, lease_ack_ready;
  lpddr5_mc_axi_lease_command_t [PORTS-1:0] lease_commands, expected_command_q;
  lpddr5_mc_axi_lease_ack_t [PORTS-1:0] lease_acks;
  logic [PORTS-1:0][63:0] owner_mask_q;
  logic [PORTS-1:0][31:0] command_token_q, imported_next_q, revoke_watermark_q;
  logic [PORTS-1:0] command_pending_q, ack_received_q, revoke_targets_q, revoke_sent_q;
  logic [31:0] boot_epoch_q;
  logic revoke_complete;
  int imported_count;
  logic [PORTS-1:0] import_fire;
  logic [PORTS-1:0] endpoint_bready, endpoint_rready, endpoint_retire_ready;
  lpddr5_mc_axi_proposal_t [PORTS-1:0] proposal, accepted;
  lpddr5_mc_axi_b_packet_t [PORTS-1:0] b_packets;
  lpddr5_mc_axi_r_packet_t [PORTS-1:0] r_packets;
  typedef enum logic [2:0] {IDLE, SEND_REVOKE, WAIT_DRAIN, SEND_GRANT, WAIT_GRANT_ACK} state_t;
  state_t state_q;
  localparam int PORT_BITS = PORTS > 1 ? $clog2(PORTS) : 1;
  logic [PORT_BITS-1:0] selected_q, turn_q;
  int selected;
  logic [63:0] request_footprint_q;
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
    && (&endpoint_transport_reset_ready) && host_settled_o && !admission_busy_o && !rearming_q;
  assign rearm = rearm_i && rearm_ready_o;
  always_ff @(posedge core_clk_i or negedge core_reset_n) begin
    if (!core_reset_n) begin
      was_active_q <= 0; quiesce_request_q <= 0; quiesce_issued_q <= 0; enable_seen_q <= 0;
    end else begin
      if (initialized_o) was_active_q <= 1;
      enable_seen_q <= enable_i;
      if (enable_i) quiesce_issued_q <= 0;
      // Never wrap a one-bit request over an unacknowledged generation.
      // Keeping issued low fences all admission until the new token publishes.
      else if (!quiesce_issued_q && endpoint_quiesce_ack == {PORTS{quiesce_request_q}}) begin
        quiesce_request_q <= ~quiesce_request_q; quiesce_issued_q <= 1;
      end
    end
  end
  always_ff @(posedge core_clk_i or negedge link_reset_n) begin
    if (!link_reset_n) begin
      state_q <= IDLE; selected_q <= 0; turn_q <= 0; request_footprint_q <= '0;
      rearming_q <= 0; protocol_fault_q <= 0; accepted_addresses_o <= 0; grant_cycles_o <= 0;
      owner_mask_q <= 0; command_token_q <= 0; imported_next_q <= 0; revoke_watermark_q <= 0;
      command_pending_q <= 0; ack_received_q <= 0; revoke_targets_q <= 0; revoke_sent_q <= 0;
      expected_command_q <= '0; boot_epoch_q <= 0;
    end else begin
      if (rearm) begin
        rearming_q <= 1; protocol_fault_q <= 0;
        owner_mask_q <= 0; command_token_q <= 0; command_pending_q <= 0; ack_received_q <= 0;
        revoke_targets_q <= 0; revoke_sent_q <= 0; state_q <= IDLE;
        if (cfg_i.boot_epoch != boot_epoch_q) imported_next_q <= 0;
        boot_epoch_q <= cfg_i.boot_epoch;
      end else if (rearming_q && initialized_o) rearming_q <= 0;
      if (state_q != IDLE) grant_cycles_o <= grant_cycles_o+1;
      if (imported_count != 0) accepted_addresses_o <= accepted_addresses_o+64'(imported_count);
      for (int p=0;p<PORTS;p++) begin
        if (import_fire[p]) imported_next_q[p] <= imported_next_q[p]+1;
        if (accepted_valid[p] && initialized_o && !fault_o
            && (accepted[p].boot_epoch != cfg_i.boot_epoch || accepted[p].config_epoch != cfg_i.config_epoch
                || accepted[p].address.sequence_number != imported_next_q[p]
                || accepted[p].footprint == 0
                || (accepted[p].footprint & owner_mask_q[p]) != accepted[p].footprint)) protocol_fault_q <= 1;
        if (lease_valid[p] && lease_ready[p]) begin
          command_token_q[p] <= command_token_q[p]+1;
          expected_command_q[p] <= lease_commands[p]; command_pending_q[p] <= 1; ack_received_q[p] <= 0;
          if (state_q == SEND_REVOKE) revoke_sent_q[p] <= 1;
        end
        if (lease_ack_valid[p] && lease_ack_ready[p]) begin
          if (!command_pending_q[p] || lease_acks[p].command != expected_command_q[p]) protocol_fault_q <= 1;
          else begin
            command_pending_q[p] <= 0; ack_received_q[p] <= 1;
            revoke_watermark_q[p] <= lease_acks[p].accepted_next_sequence;
          end
        end
        if ((state_q == SEND_REVOKE && revoke_targets_q[p] && !revoke_sent_q[p]
              || state_q == SEND_GRANT && selected_q == PORT_BITS'(p))
            && command_token_q[p] == 32'hffffffff) protocol_fault_q <= 1;
      end
      case (state_q)
        IDLE: if (selected >= 0) begin
          logic conflict;
          conflict = 0;
          for (int p=0;p<PORTS;p++) if (p != selected)
            conflict |= |(owner_mask_q[p] & proposal[selected].footprint);
          request_footprint_q <= proposal[selected].footprint; selected_q <= PORT_BITS'(selected);
          turn_q <= selected == PORTS-1 ? PORT_BITS'(0) : PORT_BITS'(selected+1);
          if (proposal[selected].boot_epoch != cfg_i.boot_epoch
              || proposal[selected].config_epoch != cfg_i.config_epoch
              || proposal[selected].footprint == 0) protocol_fault_q <= 1;
          else if (conflict) begin
            for (int p=0;p<PORTS;p++) revoke_targets_q[p] <= owner_mask_q[p] != 0;
            revoke_sent_q <= 0; ack_received_q <= 0; state_q <= SEND_REVOKE;
          end else begin
            owner_mask_q[selected] <= owner_mask_q[selected] | proposal[selected].footprint;
            state_q <= SEND_GRANT;
          end
        end
        SEND_REVOKE: if ((revoke_sent_q & revoke_targets_q) == revoke_targets_q) state_q <= WAIT_DRAIN;
        WAIT_DRAIN: if (revoke_complete) begin
          owner_mask_q <= 0; owner_mask_q[selected_q] <= request_footprint_q;
          state_q <= SEND_GRANT;
        end
        SEND_GRANT: if (lease_valid[selected_q] && lease_ready[selected_q]) state_q <= WAIT_GRANT_ACK;
        WAIT_GRANT_ACK: if (ack_received_q[selected_q]) state_q <= IDLE;
        default: protocol_fault_q <= 1;
      endcase
    end
  end
  always_comb begin
    selected = -1;
    proposal_ready = 0; lease_valid = 0; accepted_ready = 0;
    lease_ack_ready = {PORTS{initialized_o && !fault_o}};
    lease_commands = '0; import_fire = 0; imported_count = 0;
    core_awvalid_o = 0; core_arvalid_o = 0; revoke_complete = 1;
    for (int p=0;p<PORTS;p++) begin
      core_aw_o[p] = accepted[p].address;
      core_ar_o[p] = accepted[p].address;
      if (revoke_targets_q[p])
        revoke_complete &= ack_received_q[p] && imported_next_q[p] == revoke_watermark_q[p];
      // Imports, W, completions and events continue throughout revocation.
      // Old rights remain reserved until the exact frozen watermark imports.
      if (accepted_valid[p] && initialized_o && !fault_o
          && accepted[p].boot_epoch == cfg_i.boot_epoch && accepted[p].config_epoch == cfg_i.config_epoch
          && accepted[p].address.sequence_number == imported_next_q[p]
          && accepted[p].footprint != 0
          && (accepted[p].footprint & owner_mask_q[p]) == accepted[p].footprint) begin
        if (accepted[p].write_access) begin
          core_awvalid_o[p] = 1; accepted_ready[p] = core_awready_i[p];
        end else begin
          core_arvalid_o[p] = 1; accepted_ready[p] = core_arready_i[p];
        end
      end
      import_fire[p] = accepted_valid[p] && accepted_ready[p];
      imported_count += int'(import_fire[p]);
      lease_commands[p].boot_epoch = cfg_i.boot_epoch;
      lease_commands[p].config_epoch = cfg_i.config_epoch;
      lease_commands[p].token = command_token_q[p]+1;
      if (state_q == SEND_REVOKE) begin
        lease_commands[p].revoke = 1; lease_commands[p].mask = owner_mask_q[p];
        lease_valid[p] = revoke_targets_q[p] && !revoke_sent_q[p]
          && command_token_q[p] != 32'hffffffff && !fault_o;
      end else if (state_q == SEND_GRANT && selected_q == PORT_BITS'(p)) begin
        lease_commands[p].mask = owner_mask_q[p];
        lease_valid[p] = command_token_q[p] != 32'hffffffff && !fault_o;
      end
    end
    // Round-robin miss selection prevents a continuously missing source from
    // starving an older request while established hits continue independently.
    for (int offset=0;offset<PORTS;offset++) begin
      int p;
      p = (int'(turn_q)+offset)%PORTS;
      if (selected < 0 && initialized_o && !fault_o && proposal_valid[p]
          && int'(proposal[p].address.slot)<DEPTH
          && core_slot_available_i[p][proposal[p].write_access ? 0 : 1][$clog2(DEPTH)'(proposal[p].address.slot)]) selected = p;
    end
    if (state_q == IDLE && selected >= 0) proposal_ready[selected] = 1;
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
  for (genvar p=0;p<PORTS;p++) begin : lease_checks
    assert property (@(posedge core_clk_i) disable iff (!initialized_o || fault_o)
      lease_valid[p] && !lease_ready[p] |=> lease_valid[p] && $stable(lease_commands[p]));
    for (genvar q=0;q<p;q++) begin : ownership
      assert property (@(posedge core_clk_i) disable iff (!initialized_o)
        (owner_mask_q[p] & owner_mask_q[q]) == 0);
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
      .lease_valid_i(lease_valid[p]),.lease_ready_o(lease_ready[p]),.lease_i(lease_commands[p]),
      .lease_ack_valid_o(lease_ack_valid[p]),.lease_ack_ready_i(lease_ack_ready[p]),.lease_ack_o(lease_acks[p]),
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
