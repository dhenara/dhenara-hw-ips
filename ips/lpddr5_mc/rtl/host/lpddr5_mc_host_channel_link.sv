// Payload crossings for one channel. Faults require the host's acceptance
// ledger to settle; flushing these transports is never host completion.
module lpddr5_mc_host_channel_link #(
  parameter int DEPTH = 64
) (
  input logic hclk_i, hrst_ni, h_enable_i, h_rearm_i,
  input logic cclk_i, crst_ni, c_enable_i, c_rearm_i,
  output logic h_fault_o, c_fault_o,
  output logic h_rearm_ready_o, c_rearm_ready_o,
  input logic h_request_valid_i,
  output logic h_request_ready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_granule_t h_request_i,
  input logic [31:0] h_boot_epoch_i, h_config_epoch_i,
  output logic c_request_valid_o,
  input logic c_request_ready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_granule_t c_request_o,
  output logic [31:0] c_boot_epoch_o, c_config_epoch_o,
  input logic c_completion_valid_i,
  output logic c_completion_ready_o,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_completion_t c_completion_i,
  output logic h_completion_valid_o,
  input logic h_completion_ready_i,
  output lpddr5_mc_host_pkg::lpddr5_mc_host_completion_t h_completion_o,
  input logic h_feedback_valid_i,
  output logic h_feedback_ready_o,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_feedback_t h_feedback_i,
  output logic c_feedback_valid_o,
  input logic c_feedback_ready_i,
  output lpddr5_mc_host_pkg::lpddr5_mc_host_feedback_t c_feedback_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_host_pkg::*;
  localparam int REQUEST_WIDTH = $bits(lpddr5_mc_granule_t)+64;
  logic [2:0] h_faults, c_faults;
  logic [2:0] h_reset_ready, c_reset_ready;
  assign h_rearm_ready_o = &h_reset_ready;
  assign c_rearm_ready_o = &c_reset_ready;
  assign h_fault_o = |h_faults;
  assign c_fault_o = |c_faults;
  lpddr5_mc_async_fifo #(.WIDTH(REQUEST_WIDTH),.DEPTH(DEPTH)) request_fifo (
    .wclk_i(hclk_i),.wrst_ni(hrst_ni),.w_enable_i(h_enable_i),.w_rearm_i(h_rearm_i),
    .wvalid_i(h_request_valid_i),.wready_o(h_request_ready_o),
    .wdata_i({h_boot_epoch_i,h_config_epoch_i,h_request_i}),.w_fault_o(h_faults[0]),
    .w_rearm_ready_o(h_reset_ready[0]), .r_rearm_ready_o(c_reset_ready[0]),
    .rclk_i(cclk_i),.rrst_ni(crst_ni),.r_enable_i(c_enable_i),.r_rearm_i(c_rearm_i),
    .rvalid_o(c_request_valid_o),.rready_i(c_request_ready_i),
    .rdata_o({c_boot_epoch_o,c_config_epoch_o,c_request_o}),.r_fault_o(c_faults[0])
  );
  lpddr5_mc_async_fifo #(.WIDTH($bits(lpddr5_mc_host_completion_t)),.DEPTH(DEPTH)) completion_fifo (
    .wclk_i(cclk_i),.wrst_ni(crst_ni),.w_enable_i(c_enable_i),.w_rearm_i(c_rearm_i),
    .wvalid_i(c_completion_valid_i),.wready_o(c_completion_ready_o),.wdata_i(c_completion_i),.w_fault_o(c_faults[1]),
    .w_rearm_ready_o(c_reset_ready[1]), .r_rearm_ready_o(h_reset_ready[1]),
    .rclk_i(hclk_i),.rrst_ni(hrst_ni),.r_enable_i(h_enable_i),.r_rearm_i(h_rearm_i),
    .rvalid_o(h_completion_valid_o),.rready_i(h_completion_ready_i),.rdata_o(h_completion_o),.r_fault_o(h_faults[1])
  );
  // At most one exposure and one handshake record per live posted tag. The
  // producer retains each record until this queue acknowledges its transfer.
  lpddr5_mc_async_fifo #(.WIDTH($bits(lpddr5_mc_host_feedback_t)),.DEPTH(DEPTH)) feedback_fifo (
    .wclk_i(hclk_i),.wrst_ni(hrst_ni),.w_enable_i(h_enable_i),.w_rearm_i(h_rearm_i),
    .wvalid_i(h_feedback_valid_i),.wready_o(h_feedback_ready_o),.wdata_i(h_feedback_i),.w_fault_o(h_faults[2]),
    .w_rearm_ready_o(h_reset_ready[2]), .r_rearm_ready_o(c_reset_ready[2]),
    .rclk_i(cclk_i),.rrst_ni(crst_ni),.r_enable_i(c_enable_i),.r_rearm_i(c_rearm_i),
    .rvalid_o(c_feedback_valid_o),.rready_i(c_feedback_ready_i),.rdata_o(c_feedback_o),.r_fault_o(c_faults[2])
  );
endmodule
