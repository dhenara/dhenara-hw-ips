// Controller composition. APB, AXI and CK run on independent clocks. Bundled
// mailboxes carry every multibit control/image/status crossing; single-bit
// publication/fault indications only fence admission and never publish data.
module lpddr5_mc #(
  parameter int PORTS=2,CHANNELS=2,RANKS=1,DATA_BITS=128,
  parameter int ID_BITS=8,ADDRESS_BITS=40,DEPTH=64,
  parameter bit PER_BANK_REFRESH=0,
  parameter int PUBLICATION_TIMEOUT=65536
) (
  input logic clk_ck_i,pclk_i,rst_ni,
  input logic psel_i,penable_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_apb_request_t apb_i,
  output logic pready_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_apb_response_t apb_o,
  output logic irq_o,
  // Integration-qualified immutable descriptors, stable for a complete boot.
  input lpddr5_mc_channel_pkg::lpddr5_mc_profile_t [2:0] qualified_profiles_i,
  input logic external_fault_i,
  output logic destructive_reset_request_o,
  input logic [CHANNELS-1:0] destructive_reset_confirmed_i,
  input logic [PORTS-1:0] axi_clk_i,axi_rst_ni,
  input logic [PORTS-1:0] awvalid_i,arvalid_i,wvalid_i,
  output logic [PORTS-1:0] awready_o,arready_o,wready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_address_t [PORTS-1:0] aw_i,ar_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_write_t [PORTS-1:0] w_i,
  output logic [PORTS-1:0] bvalid_o,rvalid_o,
  input logic [PORTS-1:0] bready_i,rready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_b_t [PORTS-1:0] b_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_read_t [PORTS-1:0] r_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_management_in_t [CHANNELS-1:0] management_i,
  input logic [CHANNELS-1:0][1:0] rank_report_valid_i,rank_self_refresh_i,phy_refresh_done_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_management_out_t [CHANNELS-1:0] management_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_ca_t [CHANNELS-1:0] ca_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_wck4_t [CHANNELS-1:0] wck_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_write_bus4_t [CHANNELS-1:0] write_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_read_request4_t [CHANNELS-1:0] read_request_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_return4_t [CHANNELS-1:0] return_i,
  output logic enabled_o,fault_o,host_settled_o,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_status_t [CHANNELS-1:0] status_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_apb_metadata_pkg::*;
  import lpddr5_mc_apb_pkg::*;
  import lpddr5_mc_host_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic crst_n,prst_n,cold_rst_n,cold_prst_n;
  // Epoch custody survives a core/APB reset while any AXI ledger survives.
  // Cold startup must assert the common reset and every AXI reset together.
  lpddr5_mc_reset_sync cold_reset(.clk_i(clk_ck_i),
    .arst_ni(rst_ni || (|axi_rst_ni)),.rst_no(cold_rst_n));
  lpddr5_mc_reset_sync cold_p_reset(.clk_i(pclk_i),
    .arst_ni(rst_ni || (|axi_rst_ni)),.rst_no(cold_prst_n));
  lpddr5_mc_reset_sync c_reset(.clk_i(clk_ck_i),.arst_ni(rst_ni),.rst_no(crst_n));
  lpddr5_mc_reset_sync p_reset(.clk_i(pclk_i),.arst_ni(rst_ni),.rst_no(prst_n));
  logic [CHANNELS-1:0] p_fault,c_fault,p_ready,c_ready;
  logic [2:0] p_misc_fault,c_misc_fault,p_misc_ready,c_misc_ready;
  logic p_rearm_q,c_rearm_q,p_armed_q,c_armed_q,p_started_q,c_started_q;
  logic p_transport_fault,c_transport_fault;
  assign p_transport_fault=p_started_q && ((|p_fault) || (|p_misc_fault));
  assign c_transport_fault=c_started_q && ((|c_fault) || (|c_misc_fault));
  always_ff @(posedge pclk_i or negedge prst_n) begin
    if(!prst_n) begin p_rearm_q<=0;p_armed_q<=0;p_started_q<=0;end
    else begin
      p_rearm_q<=0;
      if(!p_armed_q && (&p_ready) && (&p_misc_ready)) begin p_rearm_q<=1;p_armed_q<=1;end
      if(p_armed_q && !(|p_fault) && !(|p_misc_fault)) p_started_q<=1;
    end
  end
  always_ff @(posedge clk_ck_i or negedge crst_n) begin
    if(!crst_n) begin c_rearm_q<=0;c_armed_q<=0;c_started_q<=0;end
    else begin
      c_rearm_q<=0;
      if(!c_armed_q && (&c_ready) && (&c_misc_ready)) begin c_rearm_q<=1;c_armed_q<=1;end
      if(c_armed_q && !(|c_fault) && !(|c_misc_fault)) c_started_q<=1;
    end
  end
  logic apb_valid,apb_fault,apb_busy;
  logic [31:0] apb_epoch,publication_boot;
  lpddr5_mc_register_image_t [CHANNELS-1:0] active_image,publication_image;
  lpddr5_mc_config_mailbox_t [CHANNELS-1:0] publication;
  logic [CHANNELS-1:0] publication_valid,publication_ready,p_ack_valid,p_ack_ready;
  lpddr5_mc_publication_ack_t [CHANNELS-1:0] p_ack,c_ack;
  logic [CHANNELS-1:0] c_config_valid,c_config_ready,c_ack_valid,c_ack_ready,adapter_fault;
  lpddr5_mc_configuration_payload_t [CHANNELS-1:0] p_config,c_config;
  lpddr5_mc_channel_config_t [CHANNELS-1:0] channel_config;
  lpddr5_mc_config_mailbox_t [CHANNELS-1:0] mailbox_value;
  logic [CHANNELS-1:0] prepared,prepare_error,fsp_ready,fsp_declined,fsp_command,command_ready,command_rejected;
  logic [CHANNELS-1:0][31:0] prepared_epoch,committed_epoch;
  logic [CHANNELS-1:0][1:0] target_fsp;
  logic [CHANNELS-1:0][4:0] target_frequency;
  logic [CHANNELS-1:0] notify_valid,notify_ready,c_status_valid,c_status_ready,p_status_valid,p_status_ready;
  lpddr5_mc_channel_notification_t [CHANNELS-1:0] notification;
  lpddr5_mc_apb_pkg::lpddr5_mc_channel_status_t [CHANNELS-1:0] c_status,p_status;
  logic p_command_valid,p_command_ready,c_command_valid,c_command_ready;
  lpddr5_mc_control_command_t p_command,c_command;
  logic host_cfg_svalid,host_cfg_sready,host_cfg_dvalid,host_cfg_dready;
  lpddr5_mc_host_config_t decoded_host,host_cfg_sq,host_cfg_d,installed_host;
  logic [31:0] sent_epoch_q,sent_boot_q;
  lpddr5_mc_host_register_decode host_decoder(.image_i(active_image[0]),.boot_epoch_i(publication_boot),
    .config_epoch_i(apb_epoch),.config_o(decoded_host));
  always_ff @(posedge pclk_i or negedge prst_n) begin
    if(!prst_n) begin host_cfg_svalid<=0;host_cfg_sq<='0;sent_epoch_q<=0;sent_boot_q<=0;end
    else begin
      if(host_cfg_svalid && host_cfg_sready) host_cfg_svalid<=0;
      if(!host_cfg_svalid && apb_valid && (apb_epoch!=sent_epoch_q || publication_boot!=sent_boot_q)) begin
        host_cfg_sq<=decoded_host;host_cfg_svalid<=1;sent_epoch_q<=apb_epoch;sent_boot_q<=publication_boot;
      end
    end
  end
  lpddr5_mc_mailbox #(.WIDTH($bits(lpddr5_mc_host_config_t))) host_configuration_crossing (
    .sclk_i(pclk_i),.srst_ni(prst_n),.s_enable_i(1'b1),.s_rearm_i(p_rearm_q),.s_rearm_ready_o(p_misc_ready[0]),
    .svalid_i(host_cfg_svalid),.sready_o(host_cfg_sready),.sdata_i(host_cfg_sq),.s_fault_o(p_misc_fault[0]),
    .dclk_i(clk_ck_i),.drst_ni(crst_n),.d_enable_i(1'b1),.d_rearm_i(c_rearm_q),.d_rearm_ready_o(c_misc_ready[0]),
    .dvalid_o(host_cfg_dvalid),.dready_i(host_cfg_dready),.ddata_o(host_cfg_d),.d_fault_o(c_misc_fault[0]));
  lpddr5_mc_mailbox #(.WIDTH($bits(lpddr5_mc_control_command_t))) command_crossing (
    .sclk_i(pclk_i),.srst_ni(prst_n),.s_enable_i(1'b1),.s_rearm_i(p_rearm_q),.s_rearm_ready_o(p_misc_ready[1]),
    .svalid_i(p_command_valid),.sready_o(p_command_ready),.sdata_i(p_command),.s_fault_o(p_misc_fault[1]),
    .dclk_i(clk_ck_i),.drst_ni(crst_n),.d_enable_i(1'b1),.d_rearm_i(c_rearm_q),.d_rearm_ready_o(c_misc_ready[1]),
    .dvalid_o(c_command_valid),.dready_i(c_command_ready),.ddata_o(c_command),.d_fault_o(c_misc_fault[1]));
  // Sample the whole CK-owned status together. This breaks the admission /
  // settled / drain feedback path and gives the coordinator one coherent tick.
  lpddr5_mc_channel_pkg::lpddr5_mc_channel_status_t [CHANNELS-1:0] coordinator_status_q;
  always_ff @(posedge clk_ck_i or negedge crst_n) begin
    if(!crst_n) coordinator_status_q<='0;else coordinator_status_q<=status_o;
  end
  // VALID remains asserted until source READY, including while asleep.
  // Only a single-bit wake indication crosses from each AXI clock domain.
  (* ASYNC_REG="TRUE" *) logic [PORTS-1:0] wake_meta_q,wake_sync_q;
  always_ff @(posedge clk_ck_i or negedge crst_n) begin
    if(!crst_n) begin wake_meta_q<=0;wake_sync_q<=0;end
    else begin wake_meta_q<=awvalid_i | arvalid_i | wvalid_i;wake_sync_q<=wake_meta_q;end
  end
  logic quiesced,profile_ready,trained,train_request,trusted,recovery_complete;
  logic [3:0] state_value;
  logic [31:0] boot_epoch,config_epoch;
  logic [CHANNELS-1:0] management_request,coordinator_command_valid,train_ack,perf_snapshot,perf_ready,channel_reset;
  lpddr5_mc_channel_command_t [CHANNELS-1:0] coordinator_command,channel_command;
  logic [CHANNELS-1:0] channel_command_valid;
  logic [CHANNELS-1:0][31:0] train_token;
  logic host_fatal,host_recover,host_rearm,host_rearm_ready,host_initialized,host_config_active,host_fault,host_config_valid,host_config_ready;
  logic force_channel_fault,rejected,late_error;
  logic [31:0] stale_completions;
  logic [63:0] accepted_addresses,grant_cycles;
  (* ASYNC_REG="TRUE" *) logic [1:0] busy_cq,fault_cq;
  always_ff @(posedge clk_ck_i or negedge crst_n) begin
    if(!crst_n) begin busy_cq<=0;fault_cq<=0;end
    else begin busy_cq<={busy_cq[0],apb_busy};fault_cq<={fault_cq[0],apb_fault};end
  end
  typedef struct packed {
    logic enabled,quiesced,settled,fault,recovered,profile_valid,trained,train_request,trusted;
    logic [3:0] state_value;
    logic [31:0] boot_epoch,config_epoch;
    logic [1:0] active_fsp;
  } global_snapshot_t;
  global_snapshot_t global_sq,global_d,global_pq;
  logic global_svalid,global_sready,global_dvalid,recovery_pending_q;
  lpddr5_mc_register_image_t global_image;
  always_ff @(posedge clk_ck_i or negedge crst_n) begin
    if(!crst_n) begin global_sq<='0;global_svalid<=0;recovery_pending_q<=0;end
    else begin
      if(recovery_complete) recovery_pending_q<=1;
      if(global_svalid && global_sready) begin
        global_svalid<=0;
        if(global_sq.recovered) recovery_pending_q<=0;
      end
      if(!global_svalid) begin
        global_sq<='{enabled:enabled_o,quiesced:quiesced,settled:host_settled_o,fault:fault_o,
          recovered:recovery_pending_q,profile_valid:profile_ready,trained:trained,train_request:train_request,
          trusted:trusted,state_value:state_value,boot_epoch:boot_epoch,config_epoch:config_epoch,active_fsp:target_fsp[0]};
        global_svalid<=1;
      end
    end
  end
  lpddr5_mc_mailbox #(.WIDTH($bits(global_snapshot_t))) global_status_crossing (
    .sclk_i(clk_ck_i),.srst_ni(crst_n),.s_enable_i(1'b1),.s_rearm_i(c_rearm_q),.s_rearm_ready_o(c_misc_ready[2]),
    .svalid_i(global_svalid),.sready_o(global_sready),.sdata_i(global_sq),.s_fault_o(c_misc_fault[2]),
    .dclk_i(pclk_i),.drst_ni(prst_n),.d_enable_i(1'b1),.d_rearm_i(p_rearm_q),.d_rearm_ready_o(p_misc_ready[2]),
    .dvalid_o(global_dvalid),.dready_i(1'b1),.ddata_o(global_d),.d_fault_o(p_misc_fault[2]));
  always_ff @(posedge pclk_i or negedge prst_n) begin
    if(!prst_n) global_pq<='0;else if(global_dvalid) global_pq<=global_d;
  end
  always_comb begin
    for(int r=0;r<REG_COUNT;r++) global_image[r]=0;
    global_image[INDEX_STATUS]={20'b0,global_d.state_value,3'b0,global_d.fault,global_d.trusted,
      global_d.quiesced,global_d.trained,global_d.profile_valid};
    global_image[INDEX_BOOT_EPOCH]=global_d.boot_epoch;
    global_image[INDEX_TRAIN_REQUEST]={31'b0,global_d.train_request};
    global_image[INDEX_TRAIN_DONE]={31'b0,global_d.trained};
  end
  logic [2:0] last_protection;
  logic unused_global_ready;
  lpddr5_mc_apb #(.CHANNELS(CHANNELS),.PORTS(PORTS),.RANKS(RANKS),.AXI_BITS(DATA_BITS),
    .ADDRESS_BITS(ADDRESS_BITS),.QUEUE_DEPTH(DEPTH),.PUBLICATION_TIMEOUT(PUBLICATION_TIMEOUT)) apb (
    .pclk(pclk_i),.preset_n(prst_n),.cold_preset_n(cold_prst_n),.psel_i,.penable_i,.request_i(apb_i),.pready_o,.response_o(apb_o),
    .enabled_i(global_pq.enabled),.quiesced_i(global_pq.quiesced),.host_settled_i(global_pq.settled),.active_fsp_i(active_image[0][INDEX_DVFS][1:0]),
    .link_fault_i(p_transport_fault),.recovery_confirmed_i(global_dvalid && global_d.recovered),
    .global_status_valid_i(global_dvalid),.global_status_i(global_image),.global_status_ready_o(unused_global_ready),
    .status_valid_i(p_status_valid),.status_i(p_status),.status_ready_o(p_status_ready),
    .config_valid_o(publication_valid),.config_ready_i(publication_ready),.config_o(publication),.config_image_o(publication_image),.config_boot_epoch_o(publication_boot),
    .config_ack_valid_i(p_ack_valid),.config_ack_i(p_ack),.config_ack_ready_o(p_ack_ready),
    .command_valid_o(p_command_valid),.command_ready_i(p_command_ready),.command_o(p_command),.last_protection_o(last_protection),
    .irq_o,.configuration_valid_o(apb_valid),.fault_o(apb_fault),.publication_busy_o(apb_busy),.configuration_epoch_o(apb_epoch),.active_image_o(active_image));
  for(genvar c=0;c<CHANNELS;c++) begin : control
    lpddr5_mc_register_image_t snapshot;
    assign management_request[c]=management_i[c].phyupd_req || management_i[c].phymstr_req;
    assign p_config[c].publication=publication[c];assign p_config[c].boot_epoch=publication_boot;assign p_config[c].image=publication_image[c];
    lpddr5_mc_control_link crossing (
      .a_status_rst_ni(cold_prst_n),.c_status_rst_ni(cold_rst_n),
      .aclk_i(pclk_i),.arst_ni(prst_n),.a_enable_i(1'b1),.a_rearm_i(p_rearm_q),.a_fault_o(p_fault[c]),.a_rearm_ready_o(p_ready[c]),
      .cclk_i(clk_ck_i),.crst_ni(crst_n),.c_enable_i(1'b1),.c_rearm_i(c_rearm_q),.c_fault_o(c_fault[c]),.c_rearm_ready_o(c_ready[c]),
      .a_config_valid_i(publication_valid[c]),.a_config_ready_o(publication_ready[c]),.a_config_i(p_config[c]),
      .c_config_valid_o(c_config_valid[c]),.c_config_ready_i(c_config_ready[c]),.c_config_o(c_config[c]),
      .c_ack_valid_i(c_ack_valid[c]),.c_ack_ready_o(c_ack_ready[c]),.c_ack_i(c_ack[c]),
      .a_ack_valid_o(p_ack_valid[c]),.a_ack_ready_i(p_ack_ready[c]),.a_ack_o(p_ack[c]),
      .c_status_valid_i(c_status_valid[c]),.c_status_ready_o(c_status_ready[c]),.c_status_i(c_status[c]),
      .a_status_valid_o(p_status_valid[c]),.a_status_ready_i(p_status_ready[c]),.a_status_o(p_status[c]));
    lpddr5_mc_channel_control #(.CHANNEL_INDEX(c)) publication_owner (
      .clk_i(clk_ck_i),.rst_ni(crst_n),.valid_i(c_config_valid[c]),.ready_o(c_config_ready[c]),.payload_i(c_config[c]),.qualified_profiles_i,
      .ack_valid_o(c_ack_valid[c]),.ack_ready_i(c_ack_ready[c]),.ack_o(c_ack[c]),.config_o(channel_config[c]),.mailbox_o(mailbox_value[c]),
      .prepared_valid_i(prepared[c]),.prepare_error_i(prepare_error[c]),.fsp_ready_i(fsp_ready[c]),.fsp_declined_i(fsp_declined[c]),
      .prepared_epoch_i(prepared_epoch[c]),.committed_epoch_i(committed_epoch[c]),.status_i(status_o[c]),
      .fsp_command_valid_o(fsp_command[c]),.command_ready_i(command_ready[c]),.command_rejected_i(command_rejected[c]),
      .target_fsp_o(target_fsp[c]),.target_frequency_o(target_frequency[c]),.fault_o(adapter_fault[c]));
    assign channel_command_valid[c]=fsp_command[c] || coordinator_command_valid[c];
    assign channel_command[c]=fsp_command[c] ? CH_CMD_FSP : coordinator_command[c];
    assign snapshot=lpddr5_mc_integration_pkg::channel_status_image(status_o[c]);
    lpddr5_mc_status_bridge status_source (
      .clk_i(clk_ck_i),.rst_ni(cold_rst_n),.snapshot_i(snapshot),.perf_capture_i(perf_snapshot[c]),.perf_ready_o(perf_ready[c]),.notification_valid_i(notify_valid[c]),.notification_ready_o(notify_ready[c]),
      .notification_i(notification[c]),.valid_o(c_status_valid[c]),.ready_i(c_status_ready[c]),.status_o(c_status[c]));
  end
  lpddr5_mc_coordinator #(.CHANNELS(CHANNELS)) coordinator (
    .clk_i(clk_ck_i),.rst_ni(crst_n),.cold_rst_ni(cold_rst_n),.command_valid_i(c_command_valid),.command_ready_o(c_command_ready),.command_i(c_command),
    .publication_busy_i(busy_cq[1]),.external_fault_i(external_fault_i || c_transport_fault),.control_fault_i(fault_cq[1] || (|adapter_fault)),
    .management_request_i(management_request),.wake_request_i(|wake_sync_q),.configuration_valid_i(host_cfg_dvalid),.configuration_ready_o(host_cfg_dready),.configuration_i(host_cfg_d),
    .host_config_valid_o(host_config_valid),.host_config_ready_i(host_config_ready),.host_config_valid_i(host_config_active),.host_config_o(installed_host),
    .host_settled_i(host_settled_o),.host_initialized_i(host_initialized),.host_rearm_ready_i(host_rearm_ready),.host_fault_i(host_fault),
    .host_enable_o(enabled_o),.host_fatal_o(host_fatal),.host_recover_o(host_recover),.host_rearm_o(host_rearm),
    .status_i(coordinator_status_q),.command_valid_o(coordinator_command_valid),.command_ready_i(command_ready & ~fsp_command),.command_rejected_i(command_rejected),.command_o(coordinator_command),
    .train_ack_o(train_ack),.perf_snapshot_o(perf_snapshot),.perf_ready_i(perf_ready),.train_token_o(train_token),.destructive_reset_request_o,
    .destructive_reset_confirmed_i,.channel_reset_confirmed_o(channel_reset),.force_channel_fault_o(force_channel_fault),
    .fault_o,.rejected_o(rejected),.recovery_complete_o(recovery_complete),.boot_epoch_o(boot_epoch),.config_epoch_o(config_epoch),
    .profile_valid_o(profile_ready),.trained_o(trained),.train_request_o(train_request),.quiesced_o(quiesced),.trusted_o(trusted),.state_o(state_value));
  lpddr5_mc_datapath #(.PORTS(PORTS),.CHANNELS(CHANNELS),.RANKS(RANKS),.DATA_BITS(DATA_BITS),
    .ID_BITS(ID_BITS),.ADDRESS_BITS(ADDRESS_BITS),.DEPTH(DEPTH),.PER_BANK_REFRESH(PER_BANK_REFRESH)) datapath (
    .clk_ck_i,.rst_ni(crst_n),.cold_rst_ni(cold_rst_n),.enable_i(enabled_o),.fatal_i(host_fatal),.recover_i(host_recover),.rearm_i(host_rearm),
    .host_config_valid_i(host_config_valid),.host_config_ready_o(host_config_ready),.host_config_i(installed_host),
    .rearm_ready_o(host_rearm_ready),.initialized_o(host_initialized),.host_config_valid_o(host_config_active),.host_fault_o(host_fault),.host_settled_o,.late_error_o(late_error),.stale_completions_o(stale_completions),
    .axi_clk_i,.axi_rst_ni,.awvalid_i,.arvalid_i,.wvalid_i,.awready_o,.arready_o,.wready_o,.aw_i,.ar_i,.w_i,
    .bvalid_o,.rvalid_o,.bready_i,.rready_i,.b_o,.r_o,
    .channel_config_i(channel_config),.mailbox_i(mailbox_value),.command_valid_i(channel_command_valid),.command_i(channel_command),.command_ready_o(command_ready),.command_rejected_o(command_rejected),
    .reset_confirmed_i(channel_reset),.train_ack_i(train_ack),.train_token_i(train_token),.prepared_valid_o(prepared),.prepare_error_o(prepare_error),
    .fsp_ready_o(fsp_ready),.fsp_declined_o(fsp_declined),.prepared_epoch_o(prepared_epoch),.committed_epoch_o(committed_epoch),.target_fsp_i(target_fsp),.target_frequency_i(target_frequency),
    .notification_valid_o(notify_valid),.notification_ready_i(notify_ready),.notification_o(notification),.management_i,
    .rank_report_valid_i,.rank_self_refresh_i,.phy_refresh_done_i,.management_o,.ca_o,.wck_o,.write_o,.read_request_o,.return_i,.status_o,
    .accepted_addresses_o(accepted_addresses),.grant_cycles_o(grant_cycles));
  logic unused_diagnostics;
  assign unused_diagnostics=^{force_channel_fault,rejected,late_error,stale_completions,accepted_addresses,grant_cycles,
    perf_snapshot,last_protection,unused_global_ready,global_pq,active_image};
endmodule
