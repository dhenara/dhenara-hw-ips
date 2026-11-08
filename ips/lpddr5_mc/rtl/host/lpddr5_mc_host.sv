// Host assembly. Channel streams are in clk_i; instantiate one
// lpddr5_mc_host_channel_link for each independently clocked channel.
module lpddr5_mc_host #(
  parameter int PORTS=2, CHANNELS=2, RANKS=1, DATA_BITS=128,
  parameter int ID_BITS=8, ADDRESS_BITS=40, DEPTH=64
) (
  input logic clk_i, rst_ni, cold_rst_ni,enable_i, fatal_i,
  // Owner asserts only after host settlement and confirmed destructive recovery.
  input logic recover_i,
  input logic cfg_valid_i,
  output logic cfg_ready_o,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_config_t cfg_i,
  input logic rearm_i,
  output logic rearm_ready_o, initialized_o, config_valid_o, fault_o, host_settled_o,
  output logic late_error_o,
  output logic [31:0] stale_completions_o,
  input logic [PORTS-1:0] axi_clk_i, axi_rst_ni,
  input logic [PORTS-1:0] awvalid_i, arvalid_i, wvalid_i,
  output logic [PORTS-1:0] awready_o, arready_o, wready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_address_t [PORTS-1:0] aw_i, ar_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_write_t [PORTS-1:0] w_i,
  output logic [PORTS-1:0] bvalid_o, rvalid_o,
  input logic [PORTS-1:0] bready_i, rready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_b_t [PORTS-1:0] b_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_read_t [PORTS-1:0] r_o,
  output logic [CHANNELS-1:0] request_valid_o,
  input logic [CHANNELS-1:0] request_ready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_granule_t [CHANNELS-1:0] request_o,
  output logic [CHANNELS-1:0][31:0] request_boot_epoch_o, request_config_epoch_o,
  input logic [CHANNELS-1:0] completion_valid_i,
  output logic [CHANNELS-1:0] completion_ready_o,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_completion_t [CHANNELS-1:0] completion_i,
  output logic [CHANNELS-1:0] feedback_valid_o,
  input logic [CHANNELS-1:0] feedback_ready_i,
  output lpddr5_mc_host_pkg::lpddr5_mc_host_feedback_t [CHANNELS-1:0] feedback_o,
  output logic [63:0] accepted_addresses_o, grant_cycles_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_host_pkg::*;
  logic [PORTS-1:0] aw_valid, ar_valid, w_valid, aw_ready, ar_ready, w_ready;
  logic [PORTS-1:0] b_valid, r_valid, b_ready, r_ready, event_valid, event_ready;
  logic [PORTS-1:0] retire_valid, retire_ready, endpoint_late;
  logic [PORTS-1:0][1:0][DEPTH-1:0] slot_available;
  lpddr5_mc_host_address_t [PORTS-1:0] aw, ar;
  lpddr5_mc_host_write_t [PORTS-1:0] w;
  lpddr5_mc_axi_b_t [PORTS-1:0] b;
  lpddr5_mc_axi_read_t [PORTS-1:0] r;
  lpddr5_mc_host_response_event_t [PORTS-1:0] events, retire;
  logic [PORTS-1:0][5:0] b_slot, r_slot;
  logic [PORTS-1:0][15:0] b_generation, r_generation;
  logic core_fault, bridge_fault, core_settled, bridge_settled, admission_busy;
  logic core_late, monitor_q, recover_allowed, core_fatal, transport_abort, abort_settled;
  logic core_reset_n;
  lpddr5_mc_host_config_t committed_config_q;

  // The source acceptance ledgers in the bridge use their OWN AXI resets.
  // Resetting this common engine can invalidate its copies, never those ledgers.
  lpddr5_mc_reset_sync core_reset (
    .clk_i,.arst_ni(rst_ni && (&axi_rst_ni)),.rst_no(core_reset_n)
  );
  assign host_settled_o = core_settled && bridge_settled && (enable_i || !admission_busy)
    && (!(core_fault || fatal_i || (monitor_q && bridge_fault)) || abort_settled);
  assign recover_allowed = recover_i && host_settled_o && !enable_i && !admission_busy && !fatal_i;
  assign core_fatal = fatal_i || (monitor_q && bridge_fault && !recover_allowed);
  assign transport_abort = fatal_i || core_fault;
  assign fault_o = core_fault || (monitor_q && bridge_fault) || fatal_i;
  assign late_error_o = core_late || (|endpoint_late);
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) committed_config_q <= '0;
    else begin
      if (cfg_valid_i && cfg_ready_o) committed_config_q <= cfg_i;
    end
  end
  // Preserve fault observation across core reset until explicit recovery.
  always_ff @(posedge clk_i or negedge cold_rst_ni) begin
    if (!cold_rst_ni) monitor_q <= 0;
    else begin
      if (initialized_o || (rearm_i && rearm_ready_o)) monitor_q <= 1;
      if (recover_allowed) monitor_q <= 0;
    end
  end
  lpddr5_mc_axi_bridge #(.PORTS(PORTS),.CHANNELS(CHANNELS),.RANKS(RANKS),.DATA_BITS(DATA_BITS),
    .ID_BITS(ID_BITS),.ADDRESS_BITS(ADDRESS_BITS),.DEPTH(DEPTH)) bridge (
    .core_clk_i(clk_i),.core_rst_ni(rst_ni),.cold_rst_ni,.enable_i,.fatal_i(transport_abort),.core_settled_i(core_settled),
    .rearm_i,.rearm_ready_o,.initialized_o,.fault_o(bridge_fault),.host_settled_o(bridge_settled),
    .admission_busy_o(admission_busy),.cfg_i(committed_config_q),.cfg_valid_i(config_valid_o),
    .abort_settled_o(abort_settled),
    .axi_clk_i,.axi_rst_ni,.awvalid_i,.arvalid_i,.wvalid_i,.awready_o,.arready_o,.wready_o,
    .aw_i,.ar_i,.w_i,.bvalid_o,.rvalid_o,.bready_i,.rready_i,.b_o,.r_o,.late_error_o(endpoint_late),
    .core_slot_available_i(slot_available),.core_awvalid_o(aw_valid),.core_arvalid_o(ar_valid),.core_wvalid_o(w_valid),
    .core_awready_i(aw_ready),.core_arready_i(ar_ready),.core_wready_i(w_ready),
    .core_aw_o(aw),.core_ar_o(ar),.core_w_o(w),.core_bvalid_i(b_valid),.core_rvalid_i(r_valid),
    .core_bready_o(b_ready),.core_rready_o(r_ready),.core_b_i(b),.core_r_i(r),
    .core_bslot_i(b_slot),.core_rslot_i(r_slot),.core_bgeneration_i(b_generation),.core_rgeneration_i(r_generation),
    .core_retire_valid_i(retire_valid),.core_retire_ready_o(retire_ready),.core_retire_i(retire),
    .core_event_valid_o(event_valid),.core_event_ready_i(event_ready),.core_event_o(events),
    .accepted_addresses_o,.grant_cycles_o
  );
  lpddr5_mc_host_core #(.PORTS(PORTS),.CHANNELS(CHANNELS),.RANKS(RANKS),.DATA_BITS(DATA_BITS),
    .ID_BITS(ID_BITS),.ADDRESS_BITS(ADDRESS_BITS),.DEPTH(DEPTH),.POSTED_EARLY(0),.EXTERNAL_ABORT_LEDGER(1)) core (
    .clk_i,.rst_ni(core_reset_n),.enable_i(enable_i || admission_busy),.fatal_i(core_fatal),.recover_i(recover_allowed),
    .cfg_valid_i,.cfg_ready_o,.cfg_i,.config_valid_o,.fault_o(core_fault),.host_settled_o(core_settled),
    .late_error_o(core_late),.stale_completions_o,
    .aw_valid_i(aw_valid),.ar_valid_i(ar_valid),.w_valid_i(w_valid),
    .aw_ready_o(aw_ready),.ar_ready_o(ar_ready),.w_ready_o(w_ready),.aw_i(aw),.ar_i(ar),.w_i(w),
    .b_valid_o(b_valid),.r_valid_o(r_valid),.b_ready_i(b_ready),.r_ready_i(r_ready),.b_o(b),.r_o(r),
    .b_slot_o(b_slot),.r_slot_o(r_slot),.b_generation_o(b_generation),.r_generation_o(r_generation),
    .response_event_valid_i(event_valid),.response_event_ready_o(event_ready),.response_event_i(events),
    .core_slot_available_o(slot_available),.retire_valid_o(retire_valid),.retire_ready_i(retire_ready),.retire_o(retire),
    .request_valid_o,.request_ready_i,.request_o,.request_boot_epoch_o,.request_config_epoch_o,
    .completion_valid_i,.completion_ready_o,.completion_i,.feedback_valid_o,.feedback_ready_i,.feedback_o
  );
endmodule
