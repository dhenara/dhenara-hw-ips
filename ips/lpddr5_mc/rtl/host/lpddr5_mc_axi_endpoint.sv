`timescale 1ns/1ps
// Per-port acceptance ledger is reset only by the corresponding AXI reset.
// Transport resets abort messages and trigger local complete error settlement.
module lpddr5_mc_axi_endpoint #(
  parameter int PORT_INDEX = 0,
  parameter int CHANNELS = 2,
  parameter int RANKS = 1,
  parameter int DATA_BITS = 128,
  parameter int ID_BITS = 8,
  parameter int ADDRESS_BITS = 40,
  parameter int DEPTH = 64
) (
  input logic axi_clk_i, axi_rst_ni, core_clk_i, core_rst_ni, core_link_rst_ni,
  input logic core_enable_i, core_rearm_i, core_quiesce_request_i,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_config_t core_cfg_i,
  output logic core_initialized_o, core_fault_o, core_settled_o, core_abort_settled_o, core_admission_idle_o, late_error_o,
  output logic core_quiesce_ack_o,
  output logic core_transport_reset_ready_o,
  input logic awvalid_i, arvalid_i, wvalid_i,
  output logic awready_o, arready_o, wready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_address_t aw_i, ar_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_write_t w_i,
  output logic bvalid_o, rvalid_o,
  input logic bready_i, rready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_b_t b_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_read_t r_o,
  output logic proposal_valid_o,
  input logic proposal_ready_i,
  output lpddr5_mc_axi_pkg::lpddr5_mc_axi_proposal_t proposal_o,
  input logic grant_valid_i,
  output logic grant_ready_o,
  input lpddr5_mc_axi_pkg::lpddr5_mc_axi_proposal_t grant_i,
  output logic accepted_valid_o,
  input logic accepted_ready_i,
  output lpddr5_mc_axi_pkg::lpddr5_mc_axi_proposal_t accepted_o,
  output logic core_wvalid_o,
  input logic core_wready_i,
  output lpddr5_mc_host_pkg::lpddr5_mc_host_write_t core_w_o,
  input logic core_bvalid_i, core_rvalid_i,
  output logic core_bready_o, core_rready_o,
  input lpddr5_mc_axi_pkg::lpddr5_mc_axi_b_packet_t core_b_i,
  input lpddr5_mc_axi_pkg::lpddr5_mc_axi_r_packet_t core_r_i,
  input logic core_retire_valid_i,
  output logic core_retire_ready_o,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_response_event_t core_retire_i,
  output logic core_event_valid_o,
  input logic core_event_ready_i,
  output lpddr5_mc_host_pkg::lpddr5_mc_host_response_event_t core_event_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_host_pkg::*;
  import lpddr5_mc_axi_pkg::*;
  typedef struct packed {
    logic valid, retired, acknowledged, exposed;
    logic [15:0] generation;
    logic [31:0] sequence_number;
    lpddr5_mc_axi_address_t address;
    logic [8:0] w_received, r_received;
    logic [1:0] error;
  } ledger_t;
  ledger_t writes [DEPTH], reads [DEPTH];
  logic [15:0] w_generation [DEPTH], r_generation [DEPTH];
  logic [31:0] sequence_q;
  lpddr5_mc_host_config_t config_q, recovery_config_q;
  logic epoch_valid_q, ever_epoch_q, fault_q, source_settled;
  logic recovery_request_q, recovery_seen_q, recovery_ack_q, source_rearm, recovery_inflight_q;
  (* ASYNC_REG = "TRUE" *) logic [1:0] recovery_sync_q, recovery_ack_sync_q, enable_sync_q;
  (* ASYNC_REG = "TRUE" *) logic [1:0] settled_sync_q, abort_settled_sync_q, fault_sync_q, admission_idle_sync_q;
  (* ASYNC_REG = "TRUE" *) logic [1:0] quiesce_request_sync_q, quiesce_ack_sync_q;
  logic source_settled_q, source_abort_settled_q, source_admission_idle_q, source_quiesce_ack_q;
  logic axi_reset_n, source_link_reset_n, quiesce_reset_n;
  logic [8:0] local_faults, remote_faults;
  logic [8:0] local_reset_ready, remote_reset_ready;
  logic operational, candidate_valid_q, candidate_checked_q, candidate_sent_q, granted_q;
  logic [8:0] checking_beat_q;
  logic [1:0] candidate_error_q;
  lpddr5_mc_axi_proposal_t candidate_q;
  int free_write, free_read, w_owner, synthetic_b, synthetic_r;
  int write_count, read_count;
  logic proposal_local_valid, proposal_local_ready, grant_local_valid, grant_local_ready;
  lpddr5_mc_axi_proposal_t grant_local;
  logic accepted_local_valid, accepted_local_ready, w_local_valid, w_local_ready;
  lpddr5_mc_host_write_t w_local;
  logic b_local_valid, b_local_ready, r_local_valid, r_local_ready;
  lpddr5_mc_axi_b_packet_t b_local, b_held_q;
  lpddr5_mc_axi_r_packet_t r_local, r_held_q;
  logic retire_local_valid, retire_local_ready;
  lpddr5_mc_host_response_event_t retire_local;
  logic b_active_q, r_active_q, b_exposed_q;
  logic b_event_pending_q, r_event_pending_q;
  lpddr5_mc_host_response_event_t b_event_q, r_event_q, b_event_new, r_event_new;
  logic b_event_create, r_event_create;
  logic b_event_local_valid, b_event_local_ready, r_event_local_valid, r_event_local_ready;
  lpddr5_mc_host_response_event_t b_event_local, r_event_local;
  logic b_event_remote_valid, b_event_remote_ready, r_event_remote_valid, r_event_remote_ready;
  lpddr5_mc_host_response_event_t b_event_remote, r_event_remote;
  logic event_turn_q, choose_read_event;
  logic fresh_epoch;

  initial begin
    assert (DEPTH inside {16,32,64}) else $fatal(1,"AXI endpoint DEPTH");
    assert (PORT_INDEX inside {0,1}) else $fatal(1,"AXI endpoint PORT_INDEX");
  end
  // The private endpoint accepts an asynchronous local AXI reset. Only this
  // reset may discard its authoritative accepted ledger. Remote reset/fatal
  // instead aborts transport through a separate locally released reset.
  lpddr5_mc_reset_sync axi_reset (
    .clk_i(axi_clk_i),.arst_ni(axi_rst_ni),.rst_no(axi_reset_n)
  );
  lpddr5_mc_reset_sync source_link_reset (
    .clk_i(axi_clk_i),.arst_ni(core_link_rst_ni && axi_reset_n),.rst_no(source_link_reset_n)
  );

  // This bundled initialization mailbox is independent of the aborted payload
  // FIFOs. The core freezes the complete snapshot before toggling request and
  // holds it through acknowledgement; only single-bit controls synchronize.
  always_ff @(posedge core_clk_i or negedge core_link_rst_ni) begin
    if (!core_link_rst_ni) begin
      recovery_request_q <= 0;
      recovery_config_q <= '0;
      recovery_ack_sync_q <= 0;
      core_initialized_o <= 0;
      recovery_inflight_q <= 0;
      event_turn_q <= 0;
    end else begin
      recovery_ack_sync_q <= {recovery_ack_sync_q[0],recovery_ack_q};
      if (core_rearm_i) begin
        recovery_config_q <= core_cfg_i;
        recovery_request_q <= ~recovery_request_q;
        core_initialized_o <= 0;
        recovery_inflight_q <= 1;
      end else if (recovery_inflight_q && recovery_request_q == recovery_ack_sync_q[1]
                   && !(|remote_faults) && !fault_sync_q[1]) begin
        core_initialized_o <= 1; recovery_inflight_q <= 0;
      end
      if (core_event_valid_o && core_event_ready_i) event_turn_q <= ~choose_read_event;
    end
  end
  // Fatal fencing resets payload transport, but must not prevent observation
  // of actual surviving host settlement while fatal remains asserted.
  always_ff @(posedge core_clk_i or negedge core_rst_ni) begin
    if (!core_rst_ni) begin
      settled_sync_q <= 0; abort_settled_sync_q <= 0; admission_idle_sync_q <= 0; fault_sync_q <= 2'b11;
      quiesce_ack_sync_q <= 0;
    end else begin
      settled_sync_q <= {settled_sync_q[0],source_settled_q};
      // Combine before synchronization: independent settled/fault bit pipelines
      // could otherwise join a stale healthy "empty" with a new abort.
      abort_settled_sync_q <= {abort_settled_sync_q[0],source_abort_settled_q};
      admission_idle_sync_q <= {admission_idle_sync_q[0],source_admission_idle_q};
      fault_sync_q <= {fault_sync_q[0],fault_q};
      quiesce_ack_sync_q <= {quiesce_ack_sync_q[0],source_quiesce_ack_q};
    end
  end
  // Only source flops drive control synchronizers. Registering the reductions
  // prevents a decode glitch when several ledger bits change at one edge.
  always_ff @(posedge axi_clk_i or negedge axi_reset_n) begin
    if (!axi_reset_n) begin
      source_settled_q <= 0; source_abort_settled_q <= 0; source_admission_idle_q <= 0;
    end else begin
      source_settled_q <= source_settled;
      source_abort_settled_q <= fault_q && source_settled;
      source_admission_idle_q <= enable_sync_q == 0 && !candidate_valid_q;
    end
  end
  // A new disable generation needs a new acknowledgement even if an old idle
  // level is still visible while this source clock is stopped. This control
  // handshake survives payload abort, but raw core reset starts a fresh one.
  lpddr5_mc_reset_sync quiesce_reset (
    .clk_i(axi_clk_i),.arst_ni(core_rst_ni && axi_reset_n),.rst_no(quiesce_reset_n)
  );
  always_ff @(posedge axi_clk_i or negedge quiesce_reset_n) begin
    if (!quiesce_reset_n) begin
      quiesce_request_sync_q <= 0; source_quiesce_ack_q <= 0;
    end else begin
      quiesce_request_sync_q <= {quiesce_request_sync_q[0],core_quiesce_request_i};
      if (enable_sync_q == 0 && !candidate_valid_q)
        source_quiesce_ack_q <= quiesce_request_sync_q[1];
    end
  end
  assign core_settled_o = settled_sync_q[1];
  assign core_abort_settled_o = abort_settled_sync_q[1];
  assign core_admission_idle_o = admission_idle_sync_q[1];
  assign core_quiesce_ack_o = quiesce_ack_sync_q[1];
  assign core_transport_reset_ready_o = &remote_reset_ready;
  assign core_fault_o = fault_sync_q[1] || (core_initialized_o && |remote_faults);
  always_ff @(posedge axi_clk_i or negedge source_link_reset_n) begin
    if (!source_link_reset_n) begin recovery_sync_q <= 0; recovery_seen_q <= 0; recovery_ack_q <= 0; end
    else begin
      recovery_sync_q <= {recovery_sync_q[0],recovery_request_q};
      if (source_rearm) begin
        recovery_seen_q <= recovery_sync_q[1];
        recovery_ack_q <= recovery_sync_q[1];
      end
    end
  end
  always_ff @(posedge axi_clk_i or negedge axi_reset_n) begin
    if (!axi_reset_n) enable_sync_q <= 0;
    else enable_sync_q <= {enable_sync_q[0],core_enable_i};
  end
  assign fresh_epoch = !ever_epoch_q || recovery_config_q.boot_epoch > config_q.boot_epoch
    || (!fault_q && recovery_config_q.boot_epoch == config_q.boot_epoch
        && recovery_config_q.config_epoch > config_q.config_epoch);
  assign source_rearm = source_link_reset_n && recovery_sync_q[1] != recovery_seen_q
    && (&local_reset_ready) && source_settled && fresh_epoch
    && configuration_valid(recovery_config_q,CHANNELS,RANKS,ADDRESS_BITS);
  assign operational = epoch_valid_q && !fault_q && !(|local_faults) && source_link_reset_n;

`define AXI_L2C(NAME, INDEX, TYPE, SIZE, SV, SR, SD, DV, DR, DD) \
  lpddr5_mc_async_fifo #(.WIDTH($bits(TYPE)),.DEPTH(SIZE)) NAME ( \
    .wclk_i(axi_clk_i),.wrst_ni(axi_reset_n),.w_enable_i(1'b1),.w_rearm_i(source_rearm), \
    .w_rearm_ready_o(local_reset_ready[INDEX]), \
    .wvalid_i(SV),.wready_o(SR),.wdata_i(SD),.w_fault_o(local_faults[INDEX]), \
    .rclk_i(core_clk_i),.rrst_ni(core_link_rst_ni),.r_enable_i(1'b1),.r_rearm_i(core_rearm_i), \
    .r_rearm_ready_o(remote_reset_ready[INDEX]), \
    .rvalid_o(DV),.rready_i(DR),.rdata_o(DD),.r_fault_o(remote_faults[INDEX]) \
  );
`define AXI_C2L(NAME, INDEX, TYPE, SIZE, SV, SR, SD, DV, DR, DD) \
  lpddr5_mc_async_fifo #(.WIDTH($bits(TYPE)),.DEPTH(SIZE)) NAME ( \
    .wclk_i(core_clk_i),.wrst_ni(core_link_rst_ni),.w_enable_i(1'b1),.w_rearm_i(core_rearm_i), \
    .w_rearm_ready_o(remote_reset_ready[INDEX]), \
    .wvalid_i(SV),.wready_o(SR),.wdata_i(SD),.w_fault_o(remote_faults[INDEX]), \
    .rclk_i(axi_clk_i),.rrst_ni(axi_reset_n),.r_enable_i(1'b1),.r_rearm_i(source_rearm), \
    .r_rearm_ready_o(local_reset_ready[INDEX]), \
    .rvalid_o(DV),.rready_i(DR),.rdata_o(DD),.r_fault_o(local_faults[INDEX]) \
  );
  `AXI_L2C(proposals,0,lpddr5_mc_axi_proposal_t,2,proposal_local_valid,proposal_local_ready,candidate_q,
    proposal_valid_o,proposal_ready_i,proposal_o)
  `AXI_C2L(grants,1,lpddr5_mc_axi_proposal_t,2,grant_valid_i,grant_ready_o,grant_i,
    grant_local_valid,grant_local_ready,grant_local)
  `AXI_L2C(acceptances,2,lpddr5_mc_axi_proposal_t,2,accepted_local_valid,accepted_local_ready,candidate_q,
    accepted_valid_o,accepted_ready_i,accepted_o)
  `AXI_L2C(write_data,3,lpddr5_mc_host_write_t,16,w_local_valid,w_local_ready,w_local,
    core_wvalid_o,core_wready_i,core_w_o)
  `AXI_C2L(write_responses,4,lpddr5_mc_axi_b_packet_t,DEPTH,core_bvalid_i,core_bready_o,core_b_i,
    b_local_valid,b_local_ready,b_local)
  `AXI_C2L(read_responses,5,lpddr5_mc_axi_r_packet_t,DEPTH,core_rvalid_i,core_rready_o,core_r_i,
    r_local_valid,r_local_ready,r_local)
  `AXI_C2L(retirements,6,lpddr5_mc_host_response_event_t,DEPTH,core_retire_valid_i,core_retire_ready_o,core_retire_i,
    retire_local_valid,retire_local_ready,retire_local)
  `AXI_L2C(b_events,7,lpddr5_mc_host_response_event_t,DEPTH,b_event_local_valid,b_event_local_ready,b_event_local,
    b_event_remote_valid,b_event_remote_ready,b_event_remote)
  `AXI_L2C(r_events,8,lpddr5_mc_host_response_event_t,DEPTH,r_event_local_valid,r_event_local_ready,r_event_local,
    r_event_remote_valid,r_event_remote_ready,r_event_remote)
`undef AXI_L2C
`undef AXI_C2L

  always_comb begin
    free_write = -1; free_read = -1; w_owner = -1; synthetic_b = -1; synthetic_r = -1;
    write_count = 0; read_count = 0; source_settled = !candidate_valid_q && !b_active_q && !r_active_q;
    for (int i=0;i<DEPTH;i++) begin
      if (!writes[i].valid && free_write < 0 && w_generation[i] != 16'hffff) free_write = i;
      if (!reads[i].valid && free_read < 0 && r_generation[i] != 16'hffff) free_read = i;
      if (writes[i].valid) begin
        write_count++;
        source_settled &= writes[i].acknowledged;
        if (writes[i].w_received <= {1'b0,writes[i].address.len}
            && (w_owner < 0 || writes[i].sequence_number < writes[w_owner].sequence_number)) w_owner = i;
        if (!writes[i].acknowledged && (synthetic_b < 0 || writes[i].sequence_number < writes[synthetic_b].sequence_number))
          synthetic_b = i;
      end
      if (reads[i].valid) begin
        read_count++;
        source_settled &= reads[i].acknowledged;
        if (!reads[i].acknowledged && (synthetic_r < 0 || reads[i].sequence_number < reads[synthetic_r].sequence_number))
          synthetic_r = i;
      end
    end
    proposal_local_valid = candidate_valid_q && candidate_checked_q && !candidate_sent_q && operational && enable_sync_q[1];
    grant_local_ready = operational && !granted_q;
    accepted_local_valid = candidate_valid_q && granted_q && operational
      && (candidate_q.write_access ? awvalid_i : arvalid_i);
    awready_o = accepted_local_valid && candidate_q.write_access && accepted_local_ready;
    arready_o = accepted_local_valid && !candidate_q.write_access && accepted_local_ready;
    w_local = '0;
    wready_o = 0;
    if (w_owner >= 0) begin
      w_local.axi = w_i;
      w_local.slot = 6'(w_owner);
      w_local.generation = writes[w_owner].generation;
      wready_o = operational ? w_local_ready : fault_q;
    end
    w_local_valid = wvalid_i && wready_o && operational;
    bvalid_o = b_active_q; b_o = b_held_q.axi;
    rvalid_o = r_active_q; r_o = r_held_q.axi;
    b_local_ready = !b_active_q && !b_event_pending_q && b_event_local_ready;
    r_local_ready = !r_active_q && !r_event_pending_q && r_event_local_ready;
    if (!operational) begin b_local_ready = 1; r_local_ready = 1; end
    retire_local_ready = 1;

    b_event_new = '0; r_event_new = '0;
    b_event_new.slot = b_held_q.slot; b_event_new.generation = b_held_q.generation;
    b_event_new.write_response = 1; b_event_new.response = b_held_q.axi.response;
    b_event_new.exposed = b_active_q && !b_exposed_q;
    b_event_new.handshaken = b_active_q && bready_i;
    b_event_create = b_event_new.exposed || b_event_new.handshaken;
    r_event_new.slot = r_held_q.slot; r_event_new.generation = r_held_q.generation;
    r_event_new.response = r_held_q.axi.response;
    if (r_active_q && int'(r_held_q.slot)<DEPTH)
      r_event_new.exposed = !reads[$clog2(DEPTH)'(r_held_q.slot)].exposed;
    r_event_new.handshaken = r_active_q && rready_i && r_held_q.axi.last;
    r_event_create = r_event_new.exposed || r_event_new.handshaken;
    b_event_local_valid = operational && (b_event_pending_q || b_event_create);
    r_event_local_valid = operational && (r_event_pending_q || r_event_create);
    b_event_local = b_event_pending_q ? b_event_q : b_event_new;
    r_event_local = r_event_pending_q ? r_event_q : r_event_new;
    choose_read_event = r_event_remote_valid && (!b_event_remote_valid || event_turn_q);
    core_event_valid_o = b_event_remote_valid || r_event_remote_valid;
    core_event_o = choose_read_event ? r_event_remote : b_event_remote;
    b_event_remote_ready = core_event_ready_i && !choose_read_event;
    r_event_remote_ready = core_event_ready_i && choose_read_event;
  end

  always_ff @(posedge axi_clk_i or negedge axi_reset_n) begin : ledger
    if (!axi_reset_n) begin
      for (int i=0;i<DEPTH;i++) begin writes[i] <= '0; reads[i] <= '0; w_generation[i] <= 0; r_generation[i] <= 0; end
      config_q <= '0; epoch_valid_q <= 0; ever_epoch_q <= 0; fault_q <= 1; sequence_q <= 0; late_error_o <= 0;
      candidate_valid_q <= 0; candidate_checked_q <= 0; candidate_sent_q <= 0; granted_q <= 0;
      candidate_q <= '0; checking_beat_q <= 0; candidate_error_q <= 0;
      b_active_q <= 0; r_active_q <= 0; b_exposed_q <= 0;
      b_held_q <= '0; r_held_q <= '0;
      b_event_pending_q <= 0; r_event_pending_q <= 0; b_event_q <= '0; r_event_q <= '0;
    end else begin
      if (fault_q) begin candidate_valid_q <= 0; granted_q <= 0; end
      if (!enable_sync_q[1] && candidate_valid_q && !candidate_sent_q)
        candidate_valid_q <= 0;
      if (epoch_valid_q && (|local_faults)) begin
        fault_q <= 1; candidate_valid_q <= 0; granted_q <= 0;
        b_event_pending_q <= 0; r_event_pending_q <= 0;
        for (int i=0;i<DEPTH;i++) begin
          if (writes[i].valid && writes[i].error == 0) writes[i].error <= 2'b10;
          // This ledger can remain live after B was consumed while its real
          // exposure/handshake feedback and retirement are crossing the link.
          if (writes[i].valid && writes[i].exposed && writes[i].error == 0) late_error_o <= 1;
          if (reads[i].valid && reads[i].error == 0) reads[i].error <= 2'b10;
        end
        if (b_active_q && b_held_q.axi.response == 0) late_error_o <= 1;
      end
      if (source_rearm) begin
        config_q <= recovery_config_q; epoch_valid_q <= 1; ever_epoch_q <= 1; fault_q <= 0;
        candidate_valid_q <= 0; granted_q <= 0; candidate_sent_q <= 0;
        b_event_pending_q <= 0; r_event_pending_q <= 0;
        if (!ever_epoch_q || recovery_config_q.boot_epoch > config_q.boot_epoch) begin
          sequence_q <= 0;
          for (int i=0;i<DEPTH;i++) begin writes[i] <= '0; reads[i] <= '0; w_generation[i] <= 0; r_generation[i] <= 0; end
        end
      end
      // Identity exhaustion is a recovery event, never silent wrap/reuse.
      // A fresh boot epoch resets the counters only after real host settlement.
      if (operational && enable_sync_q[1] && sequence_q == 32'hffffffff && (awvalid_i || arvalid_i))
        fault_q <= 1;
      if (operational && enable_sync_q[1] && !candidate_valid_q && sequence_q != 32'hffffffff) begin
        if ((awvalid_i && free_write >= 0 && write_count < int'(config_q.write_limit[PORT_INDEX]))
            || (arvalid_i && free_read >= 0 && read_count < int'(config_q.read_limit[PORT_INDEX]))) begin
          lpddr5_mc_axi_address_t a;
          logic write_access;
          logic [5:0] slot;
          write_access = awvalid_i && free_write >= 0 && write_count < int'(config_q.write_limit[PORT_INDEX]);
          a = write_access ? aw_i : ar_i; slot = 6'(write_access ? free_write : free_read);
          candidate_q.address.axi <= a; candidate_q.address.slot <= 6'(slot);
          candidate_q.address.generation <= write_access ? w_generation[$clog2(DEPTH)'(slot)] : r_generation[$clog2(DEPTH)'(slot)];
          candidate_q.address.sequence_number <= sequence_q; candidate_q.write_access <= write_access;
          candidate_valid_q <= 1; candidate_checked_q <= 0; candidate_sent_q <= 0; checking_beat_q <= 0;
          candidate_error_q <= address_form_valid(a,DATA_BITS,ID_BITS,ADDRESS_BITS) ? 0 : 2'b11;
        end
      end
      if (candidate_valid_q && !candidate_checked_q && operational) begin
        logic mapped;
        logic [40:0] at, limit;
        lpddr5_mc_host_decode_t decoded;
        logic unused_decode;
        at = beat_address(candidate_q.address.axi,checking_beat_q);
        limit = beat_limit(candidate_q.address.axi,checking_beat_q); mapped = 1;
        for (int lane=0;lane<DATA_BITS/8;lane++) if (at+41'(lane)<limit) begin
          decoded = decode_address(config_q,40'(at+41'(lane)),CHANNELS,RANKS);
          unused_decode = ^decoded;
          mapped &= decoded.valid;
        end
        if (!mapped) candidate_error_q <= 2'b11;
        if (checking_beat_q == {1'b0,candidate_q.address.axi.len} || candidate_error_q != 0) candidate_checked_q <= 1;
        else checking_beat_q <= checking_beat_q+1;
      end
      if (proposal_local_valid && proposal_local_ready) candidate_sent_q <= 1;
      if (grant_local_valid && grant_local_ready) begin
        if (candidate_valid_q && candidate_sent_q && grant_local == candidate_q) granted_q <= 1;
        else fault_q <= 1;
      end
      if (candidate_valid_q && operational
          && ((candidate_q.write_access && awvalid_i && aw_i != candidate_q.address.axi)
              || (!candidate_q.write_access && arvalid_i && ar_i != candidate_q.address.axi))) fault_q <= 1;
      if (awready_o || arready_o) begin
        ledger_t entry;
        entry = '0; entry.valid = 1; entry.address = candidate_q.address.axi;
        entry.generation = candidate_q.address.generation; entry.sequence_number = sequence_q; entry.error = candidate_error_q;
        if (awready_o) writes[$clog2(DEPTH)'(candidate_q.address.slot)] <= entry;
        else reads[$clog2(DEPTH)'(candidate_q.address.slot)] <= entry;
        sequence_q <= sequence_q+1;
        candidate_valid_q <= 0; candidate_sent_q <= 0; granted_q <= 0;
      end
      if (wvalid_i && wready_o && w_owner >= 0) begin
        writes[w_owner].w_received <= writes[w_owner].w_received+1;
        if (w_i.last != (writes[w_owner].w_received == {1'b0,writes[w_owner].address.len}) && writes[w_owner].error == 0)
          writes[w_owner].error <= 2'b10;
      end
      if (retire_local_valid && operational) begin
        if (retire_local.exposed || retire_local.handshaken) fault_q <= 1;
        if (int'(retire_local.slot)<DEPTH) begin
          if (retire_local.write_response && writes[$clog2(DEPTH)'(retire_local.slot)].valid
              && writes[$clog2(DEPTH)'(retire_local.slot)].generation == retire_local.generation) writes[$clog2(DEPTH)'(retire_local.slot)].retired <= 1;
          if (!retire_local.write_response && reads[$clog2(DEPTH)'(retire_local.slot)].valid
              && reads[$clog2(DEPTH)'(retire_local.slot)].generation == retire_local.generation) reads[$clog2(DEPTH)'(retire_local.slot)].retired <= 1;
          if (retire_local.response != 0 && retire_local.write_response && writes[$clog2(DEPTH)'(retire_local.slot)].error == 0)
            writes[$clog2(DEPTH)'(retire_local.slot)].error <= retire_local.response;
          if (retire_local.response != 0 && !retire_local.write_response && reads[$clog2(DEPTH)'(retire_local.slot)].error == 0)
            reads[$clog2(DEPTH)'(retire_local.slot)].error <= retire_local.response;
        end
      end
      for (int i=0;i<DEPTH;i++) begin
        if (writes[i].valid && writes[i].acknowledged && writes[i].retired) begin
          writes[i].valid <= 0; w_generation[i] <= w_generation[i]+1;
          if (w_generation[i] == 16'hfffe) fault_q <= 1;
        end
        if (reads[i].valid && reads[i].acknowledged && reads[i].retired) begin
          reads[i].valid <= 0; r_generation[i] <= r_generation[i]+1;
          if (r_generation[i] == 16'hfffe) fault_q <= 1;
        end
      end
      if (b_event_pending_q && b_event_local_ready) b_event_pending_q <= 0;
      if (r_event_pending_q && r_event_local_ready) r_event_pending_q <= 0;
      if (b_event_create && operational && (b_event_pending_q || !b_event_local_ready)) begin
        b_event_pending_q <= 1; b_event_q <= b_event_new;
        if (b_event_pending_q && !b_event_local_ready) begin
          b_event_q.exposed <= b_event_q.exposed || b_event_new.exposed;
          b_event_q.handshaken <= b_event_q.handshaken || b_event_new.handshaken;
        end
      end
      if (r_event_create && operational && (r_event_pending_q || !r_event_local_ready)) begin
        r_event_pending_q <= 1; r_event_q <= r_event_new;
        if (r_event_pending_q && !r_event_local_ready) begin
          r_event_q.exposed <= r_event_q.exposed || r_event_new.exposed;
          r_event_q.handshaken <= r_event_q.handshaken || r_event_new.handshaken;
        end
      end
      if (b_active_q) begin
        b_exposed_q <= 1;
        writes[$clog2(DEPTH)'(b_held_q.slot)].exposed <= 1;
        if (bready_i) begin b_active_q <= 0; writes[$clog2(DEPTH)'(b_held_q.slot)].acknowledged <= 1; end
      end
      if (r_active_q) begin
        reads[$clog2(DEPTH)'(r_held_q.slot)].exposed <= 1;
        if (rready_i) begin
          r_active_q <= 0;
          reads[$clog2(DEPTH)'(r_held_q.slot)].r_received <= reads[$clog2(DEPTH)'(r_held_q.slot)].r_received+1;
          if (r_held_q.axi.last) reads[$clog2(DEPTH)'(r_held_q.slot)].acknowledged <= 1;
        end
      end
      if (!b_active_q) begin
        if (operational && b_local_valid && b_local_ready) begin
          if (int'(b_local.slot)<DEPTH && writes[$clog2(DEPTH)'(b_local.slot)].valid
              && writes[$clog2(DEPTH)'(b_local.slot)].generation == b_local.generation && !writes[$clog2(DEPTH)'(b_local.slot)].acknowledged) begin
            b_held_q <= b_local; b_active_q <= 1; b_exposed_q <= 0;
            if (b_local.axi.response != 0) writes[$clog2(DEPTH)'(b_local.slot)].error <= b_local.axi.response;
          end else fault_q <= 1;
        end else if (!operational && fault_q && synthetic_b >= 0
            && writes[synthetic_b].w_received > {1'b0,writes[synthetic_b].address.len}) begin
          b_held_q.axi.id <= writes[synthetic_b].address.id;
          b_held_q.axi.response <= writes[synthetic_b].error == 3 ? 2'b11 : 2'b10;
          b_held_q.slot <= 6'(synthetic_b); b_held_q.generation <= writes[synthetic_b].generation;
          b_active_q <= 1; b_exposed_q <= 0;
        end
      end
      if (!r_active_q) begin
        if (operational && r_local_valid && r_local_ready) begin
          if (int'(r_local.slot)<DEPTH && reads[$clog2(DEPTH)'(r_local.slot)].valid
              && reads[$clog2(DEPTH)'(r_local.slot)].generation == r_local.generation && !reads[$clog2(DEPTH)'(r_local.slot)].acknowledged) begin
            r_held_q <= r_local; r_active_q <= 1;
            if (r_local.axi.response != 0) reads[$clog2(DEPTH)'(r_local.slot)].error <= r_local.axi.response;
          end else fault_q <= 1;
        end else if (!operational && fault_q && synthetic_r >= 0) begin
          r_held_q.axi.id <= reads[synthetic_r].address.id; r_held_q.axi.data <= 0;
          r_held_q.axi.response <= reads[synthetic_r].error == 3 ? 2'b11 : 2'b10;
          r_held_q.axi.last <= reads[synthetic_r].r_received == {1'b0,reads[synthetic_r].address.len};
          r_held_q.slot <= 6'(synthetic_r); r_held_q.generation <= reads[synthetic_r].generation; r_active_q <= 1;
        end
      end
    end
  end
endmodule
