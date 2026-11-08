// Common-CK host/channel composition; AXI ports retain independent clocks and
// source settlement ledgers. The control coordinator owns admission, common
// publication and destructive-recovery sequencing through explicit ports.
module lpddr5_mc_datapath #(
  parameter int PORTS=2,CHANNELS=2,RANKS=1,DATA_BITS=128,
  parameter int ID_BITS=8,ADDRESS_BITS=40,DEPTH=64,
  parameter bit PER_BANK_REFRESH=0
) (
  input logic clk_ck_i,rst_ni,cold_rst_ni,enable_i,fatal_i,recover_i,rearm_i,
  input logic host_config_valid_i,
  output logic host_config_ready_o,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_config_t host_config_i,
  output logic rearm_ready_o,initialized_o,host_config_valid_o,host_fault_o,host_settled_o,late_error_o,
  output logic [31:0] stale_completions_o,
  input logic [PORTS-1:0] axi_clk_i,axi_rst_ni,
  input logic [PORTS-1:0] awvalid_i,arvalid_i,wvalid_i,
  output logic [PORTS-1:0] awready_o,arready_o,wready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_address_t [PORTS-1:0] aw_i,ar_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_write_t [PORTS-1:0] w_i,
  output logic [PORTS-1:0] bvalid_o,rvalid_o,
  input logic [PORTS-1:0] bready_i,rready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_b_t [PORTS-1:0] b_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_read_t [PORTS-1:0] r_o,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_config_t [CHANNELS-1:0] channel_config_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_config_mailbox_t [CHANNELS-1:0] mailbox_i,
  input logic [CHANNELS-1:0] command_valid_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_command_t [CHANNELS-1:0] command_i,
  output logic [CHANNELS-1:0] command_ready_o,command_rejected_o,
  input logic [CHANNELS-1:0] reset_confirmed_i,train_ack_i,
  input logic [CHANNELS-1:0][31:0] train_token_i,
  output logic [CHANNELS-1:0] prepared_valid_o,prepare_error_o,fsp_ready_o,fsp_declined_o,
  output logic [CHANNELS-1:0][31:0] prepared_epoch_o,committed_epoch_o,
  input logic [CHANNELS-1:0][1:0] target_fsp_i,
  input logic [CHANNELS-1:0][4:0] target_frequency_i,
  output logic [CHANNELS-1:0] notification_valid_o,
  input logic [CHANNELS-1:0] notification_ready_i,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_notification_t [CHANNELS-1:0] notification_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_management_in_t [CHANNELS-1:0] management_i,
  input logic [CHANNELS-1:0][1:0] rank_report_valid_i,rank_self_refresh_i,phy_refresh_done_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_management_out_t [CHANNELS-1:0] management_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_ca_t [CHANNELS-1:0] ca_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_wck4_t [CHANNELS-1:0] wck_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_write_bus4_t [CHANNELS-1:0] write_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_read_request4_t [CHANNELS-1:0] read_request_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_return4_t [CHANNELS-1:0] return_i,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_status_t [CHANNELS-1:0] status_o,
  output logic [63:0] accepted_addresses_o,grant_cycles_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_host_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  import lpddr5_mc_integration_pkg::*;
  logic [CHANNELS-1:0] request_valid,request_ready,completion_valid,completion_ready,feedback_valid;
  lpddr5_mc_granule_t [CHANNELS-1:0] requests;
  logic [CHANNELS-1:0][31:0] request_boot,request_epoch;
  lpddr5_mc_host_completion_t [CHANNELS-1:0] host_completions;
  lpddr5_mc_host_feedback_t [CHANNELS-1:0] feedback;
  lpddr5_mc_channel_completion_t [CHANNELS-1:0] channel_completions;
  lpddr5_mc_channel_response_feedback_t [CHANNELS-1:0] channel_feedback_value;
  lpddr5_mc_host #(.PORTS(PORTS),.CHANNELS(CHANNELS),.RANKS(RANKS),.DATA_BITS(DATA_BITS),
    .ID_BITS(ID_BITS),.ADDRESS_BITS(ADDRESS_BITS),.DEPTH(DEPTH)) host (
    .clk_i(clk_ck_i),.rst_ni,.cold_rst_ni,.enable_i,.fatal_i,.recover_i,
    .cfg_valid_i(host_config_valid_i),.cfg_ready_o(host_config_ready_o),.cfg_i(host_config_i),
    .rearm_i,.rearm_ready_o,.initialized_o,.config_valid_o(host_config_valid_o),.fault_o(host_fault_o),.host_settled_o,.late_error_o,.stale_completions_o,
    .axi_clk_i,.axi_rst_ni,.awvalid_i,.arvalid_i,.wvalid_i,.awready_o,.arready_o,.wready_o,.aw_i,.ar_i,.w_i,
    .bvalid_o,.rvalid_o,.bready_i,.rready_i,.b_o,.r_o,
    .request_valid_o(request_valid),.request_ready_i(request_ready),.request_o(requests),.request_boot_epoch_o(request_boot),.request_config_epoch_o(request_epoch),
    .completion_valid_i(completion_valid),.completion_ready_o(completion_ready),.completion_i(host_completions),
    // The conservative channel has transferred final integrity at completion.
    // Its feedback input has no backpressure; host RAM still owns actual AXI
    // exposure/handshake and retirement before releasing its response credit.
    .feedback_valid_o(feedback_valid),.feedback_ready_i({CHANNELS{1'b1}}),.feedback_o(feedback),.accepted_addresses_o,.grant_cycles_o);
  for(genvar c=0;c<CHANNELS;c++) begin : channels
    logic first_valid;
    logic [63:0] first_version;
    logic [31:0] first_boot,first_epoch;
    logic unused_diagnostic;
    assign unused_diagnostic=^{first_valid,first_version,first_boot,first_epoch};
    assign host_completions[c]=host_completion(channel_completions[c]);
    assign channel_feedback_value[c]=channel_feedback(feedback[c]);
    lpddr5_mc_channel #(.DEPTH(DEPTH),.CHANNEL_INDEX(c),.PER_BANK_REFRESH(PER_BANK_REFRESH)) channel (
      .clk_ck_i,.rst_ni,.cold_rst_ni,.force_fault_i(fatal_i),.request_valid_i(request_valid[c]),.request_ready_o(request_ready[c]),.request_i(requests[c]),
      .request_boot_epoch_i(request_boot[c]),.request_config_epoch_i(request_epoch[c]),
      .completion_valid_o(completion_valid[c]),.completion_ready_i(completion_ready[c]),.completion_o(channel_completions[c]),
      .feedback_valid_i(feedback_valid[c]),.feedback_i(channel_feedback_value[c]),.host_settled_i(host_settled_o),.reset_confirmed_i(reset_confirmed_i[c]),
      .command_valid_i(command_valid_i[c]),.command_i(command_i[c]),.command_ready_o(command_ready_o[c]),.command_rejected_o(command_rejected_o[c]),
      .train_ack_i(train_ack_i[c]),.train_token_i(train_token_i[c]),.config_i(channel_config_i[c]),.mailbox_i(mailbox_i[c]),
      .prepared_valid_o(prepared_valid_o[c]),.prepare_error_o(prepare_error_o[c]),.prepared_epoch_o(prepared_epoch_o[c]),.committed_epoch_o(committed_epoch_o[c]),
      .fsp_ready_o(fsp_ready_o[c]),.fsp_declined_o(fsp_declined_o[c]),.target_fsp_i(target_fsp_i[c]),.target_frequency_i(target_frequency_i[c]),
      .notification_valid_o(notification_valid_o[c]),.notification_ready_i(notification_ready_i[c]),.notification_events_o(notification_o[c].events),
      .notification_error_valid_o(notification_o[c].error_valid),.notification_error_o(notification_o[c].error),
      .notification_boot_epoch_o(notification_o[c].boot_epoch),.notification_config_epoch_o(notification_o[c].config_epoch),
      // Local software W1C does not clear the independent CK diagnostic owner.
      .first_error_clear_i(1'b0),.first_error_clear_version_i(64'd0),.first_error_valid_o(first_valid),.first_error_version_o(first_version),
      .first_error_boot_epoch_o(first_boot),.first_error_config_epoch_o(first_epoch),
      .management_i(management_i[c]),.rank_report_valid_i(rank_report_valid_i[c]),.rank_self_refresh_i(rank_self_refresh_i[c]),.phy_refresh_done_i(phy_refresh_done_i[c]),
      .management_o(management_o[c]),.ca_o(ca_o[c]),.wck_o(wck_o[c]),.write_o(write_o[c]),.read_request_o(read_request_o[c]),.return_i(return_i[c]),.status_o(status_o[c]));
  end
endmodule
