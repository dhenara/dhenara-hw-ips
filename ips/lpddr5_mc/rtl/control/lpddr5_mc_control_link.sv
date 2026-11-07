// One channel's coherent APB/CK crossing. Rearm is an explicit owner action
// after settlement, destructive reset and fresh epochs, never a constant level.
module lpddr5_mc_control_link (
  input logic aclk_i,
  input logic arst_ni,
  input logic a_enable_i,
  input logic a_rearm_i,
  output logic a_fault_o,
  output logic a_rearm_ready_o,
  input logic cclk_i,
  input logic crst_ni,
  input logic c_enable_i,
  input logic c_rearm_i,
  output logic c_fault_o,
  output logic c_rearm_ready_o,
  input logic a_config_valid_i,
  output logic a_config_ready_o,
  input lpddr5_mc_apb_pkg::lpddr5_mc_configuration_payload_t a_config_i,
  output logic c_config_valid_o,
  input logic c_config_ready_i,
  output lpddr5_mc_apb_pkg::lpddr5_mc_configuration_payload_t c_config_o,
  input logic c_ack_valid_i,
  output logic c_ack_ready_o,
  input lpddr5_mc_apb_pkg::lpddr5_mc_publication_ack_t c_ack_i,
  output logic a_ack_valid_o,
  input logic a_ack_ready_i,
  output lpddr5_mc_apb_pkg::lpddr5_mc_publication_ack_t a_ack_o,
  input logic c_status_valid_i,
  output logic c_status_ready_o,
  input lpddr5_mc_apb_pkg::lpddr5_mc_channel_status_t c_status_i,
  output logic a_status_valid_o,
  input logic a_status_ready_i,
  output lpddr5_mc_apb_pkg::lpddr5_mc_channel_status_t a_status_o
);
  import lpddr5_mc_apb_pkg::*;
  logic [2:0] a_faults, c_faults;
  logic [2:0] a_reset_ready, c_reset_ready;
  assign a_rearm_ready_o = &a_reset_ready;
  assign c_rearm_ready_o = &c_reset_ready;
  assign a_fault_o = |a_faults;
  assign c_fault_o = |c_faults;
  lpddr5_mc_mailbox #(.WIDTH($bits(lpddr5_mc_configuration_payload_t))) config_crossing (
    .sclk_i(aclk_i), .srst_ni(arst_ni), .s_enable_i(a_enable_i), .s_rearm_i(a_rearm_i),
    .svalid_i(a_config_valid_i), .sready_o(a_config_ready_o), .sdata_i(a_config_i), .s_fault_o(a_faults[0]),
    .s_rearm_ready_o(a_reset_ready[0]), .d_rearm_ready_o(c_reset_ready[0]),
    .dclk_i(cclk_i), .drst_ni(crst_ni), .d_enable_i(c_enable_i), .d_rearm_i(c_rearm_i),
    .dvalid_o(c_config_valid_o), .dready_i(c_config_ready_i), .ddata_o(c_config_o), .d_fault_o(c_faults[0])
  );
  lpddr5_mc_mailbox #(.WIDTH($bits(lpddr5_mc_publication_ack_t))) ack_crossing (
    .sclk_i(cclk_i), .srst_ni(crst_ni), .s_enable_i(c_enable_i), .s_rearm_i(c_rearm_i),
    .svalid_i(c_ack_valid_i), .sready_o(c_ack_ready_o), .sdata_i(c_ack_i), .s_fault_o(c_faults[1]),
    .s_rearm_ready_o(c_reset_ready[1]), .d_rearm_ready_o(a_reset_ready[1]),
    .dclk_i(aclk_i), .drst_ni(arst_ni), .d_enable_i(a_enable_i), .d_rearm_i(a_rearm_i),
    .dvalid_o(a_ack_valid_o), .dready_i(a_ack_ready_i), .ddata_o(a_ack_o), .d_fault_o(a_faults[1])
  );
  lpddr5_mc_mailbox #(.WIDTH($bits(lpddr5_mc_channel_status_t))) status_crossing (
    .sclk_i(cclk_i), .srst_ni(crst_ni), .s_enable_i(c_enable_i), .s_rearm_i(c_rearm_i),
    .svalid_i(c_status_valid_i), .sready_o(c_status_ready_o), .sdata_i(c_status_i), .s_fault_o(c_faults[2]),
    .s_rearm_ready_o(c_reset_ready[2]), .d_rearm_ready_o(a_reset_ready[2]),
    .dclk_i(aclk_i), .drst_ni(arst_ni), .d_enable_i(a_enable_i), .d_rearm_i(a_rearm_i),
    .dvalid_o(a_status_valid_o), .dready_i(a_status_ready_i), .ddata_o(a_status_o), .d_fault_o(a_faults[2])
  );
endmodule
