module notification_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  import lpddr5_mc_apb_metadata_pkg::*;
  import lpddr5_mc_apb_pkg::*;
  logic pclk=0,cclk=0,preset_n=0,crst_n=0,cold_preset_n=0,cold_crst_n=0;
  logic run_pclk=1;
  always #5 if(run_pclk) pclk<=~pclk;
  always #3.5 cclk<=~cclk;
  logic psel=0,penable=0,pready;
  lpddr5_mc_apb_request_t request='0;
  lpddr5_mc_apb_response_t response;
  logic [0:0] status_valid,status_ready;
  lpddr5_mc_apb_pkg::lpddr5_mc_channel_status_t [0:0] status_value;
  lpddr5_mc_register_image_t global_status,snapshot;
  logic irq,cfg_valid,fault,busy;
  logic [31:0] cfg_epoch;
  logic p_rearm=0,c_rearm=0,p_rearm_ready,c_rearm_ready,pfault,cfault;
  logic nvalid,nready,bridge_valid,bridge_ready,allow_send=0;
  lpddr5_mc_apb_pkg::lpddr5_mc_channel_status_t bridge_status;
  logic [0:0] occurrence_valid=0;
  lpddr5_mc_channel_notification_t [0:0] occurrence='0;
  lpddr5_mc_channel_notification_t notification,held,first_error;
  logic overflow,destructive=0,first_valid;
  logic [63:0] first_version;
  logic [0:0] publication_valid;
  lpddr5_mc_config_mailbox_t [0:0] config_payload;
  lpddr5_mc_register_image_t [0:0] config_image,active_image;
  logic [31:0] config_boot;
  logic global_ready,cmd_valid;
  logic [0:0] ack_ready;
  lpddr5_mc_control_command_t cmd;
  logic [2:0] protection;
  logic perf_capture=0,perf_ready;
  int checks=0,delivered=0;
  logic [31:0] data;
  logic unused_debug,unused_config_ready,unused_config_valid,unused_ack_ready,unused_ack_valid;
  lpddr5_mc_configuration_payload_t unused_config,zero_config;
  lpddr5_mc_publication_ack_t unused_ack;
  assign unused_debug=^{ack_ready,unused_config_ready,unused_config_valid,unused_ack_ready,unused_ack_valid,unused_config,unused_ack,first_error,first_valid,first_version,publication_valid,config_payload,config_image,active_image,
    config_boot,global_ready,cmd_valid,cmd,protection,cfg_valid,fault,busy,cfg_epoch};
  lpddr5_mc_apb #(.CHANNELS(1),.PORTS(1)) apb (
    .pclk,.preset_n,.cold_preset_n(cold_preset_n),.psel_i(psel),.penable_i(penable),.request_i(request),.pready_o(pready),.response_o(response),
    .enabled_i(1'b0),.quiesced_i(1'b1),.host_settled_i(1'b1),.active_fsp_i(2'd0),.link_fault_i(1'b0),.recovery_confirmed_i(1'b0),
    .global_status_valid_i(1'b1),.global_status_i(global_status),.global_status_ready_o(global_ready),
    .status_valid_i(status_valid),.status_i(status_value),.status_ready_o(status_ready),
    .config_valid_o(publication_valid),.config_ready_i(1'b1),.config_o(config_payload),.config_image_o(config_image),.config_boot_epoch_o(config_boot),
    .config_ack_valid_i(1'b0),.config_ack_i(lpddr5_mc_publication_ack_t'('0)),.config_ack_ready_o(ack_ready),
    .command_valid_o(cmd_valid),.command_ready_i(1'b1),.command_o(cmd),.last_protection_o(protection),
    .irq_o(irq),.configuration_valid_o(cfg_valid),.fault_o(fault),.publication_busy_o(busy),.configuration_epoch_o(cfg_epoch),.active_image_o(active_image));
  lpddr5_mc_notifications #(.DEPTH(2),.INPUTS(1)) queue (
    .clk_ck_i(cclk),.rst_ni(cold_crst_n),.destructive_reset_i(destructive),.occurrence_valid_i(occurrence_valid),.occurrence_i(occurrence),
    .notification_valid_o(nvalid),.notification_ready_i(nready && allow_send),.notification_o(notification),.overflow_o(overflow),
    .first_error_clear_i(1'b0),.first_error_clear_version_i(64'd0),.first_error_valid_o(first_valid),.first_error_version_o(first_version),.first_error_o(first_error));
  lpddr5_mc_status_bridge bridge (
    .clk_i(cclk),.rst_ni(cold_crst_n),.snapshot_i(snapshot),.perf_capture_i(perf_capture),.perf_ready_o(perf_ready),.notification_valid_i(nvalid && allow_send),
    .notification_ready_o(nready),.notification_i(notification),.valid_o(bridge_valid),.ready_i(bridge_ready),.status_o(bridge_status));
  lpddr5_mc_control_link crossing (
    .a_status_rst_ni(cold_preset_n),.c_status_rst_ni(cold_crst_n),
    .aclk_i(pclk),.arst_ni(preset_n),.a_enable_i(1'b1),.a_rearm_i(p_rearm),.a_fault_o(pfault),.a_rearm_ready_o(p_rearm_ready),
    .cclk_i(cclk),.crst_ni(crst_n),.c_enable_i(1'b1),.c_rearm_i(c_rearm),.c_fault_o(cfault),.c_rearm_ready_o(c_rearm_ready),
    .a_config_valid_i(1'b0),.a_config_ready_o(unused_config_ready),.a_config_i(zero_config),
    .c_config_valid_o(unused_config_valid),.c_config_ready_i(1'b1),.c_config_o(unused_config),.c_ack_valid_i(1'b0),.c_ack_ready_o(unused_ack_ready),.c_ack_i(lpddr5_mc_publication_ack_t'('0)),
    .a_ack_valid_o(unused_ack_valid),.a_ack_ready_i(1'b1),.a_ack_o(unused_ack),.c_status_valid_i(bridge_valid),.c_status_ready_o(bridge_ready),.c_status_i(bridge_status),
    .a_status_valid_o(status_valid[0]),.a_status_ready_i(status_ready[0]),.a_status_o(status_value[0]));
  always @(posedge pclk) if(status_valid[0] && status_ready[0] && status_value[0].events!=0) begin
    delivered<=delivered+1;
    if(delivered==0) assert(status_value[0].image[INDEX_BOOT_EPOCH]==7 && status_value[0].image[INDEX_CONFIG_EPOCH]==23)
      else $fatal(1,"retained occurrence provenance changed");
  end
  task automatic check(input logic condition,input string message);
    assert(condition) else $fatal(1,"%s",message);checks++;
  endtask
  task automatic access(input bit write_value,input logic [17:0] address,input logic [31:0] value);
    @(negedge pclk);psel=1;penable=0;request='{address:address,write:write_value,data:value,strobe:write_value ? 4'hf : 4'd0,protection:3'd0};
    @(negedge pclk);penable=1;#1;check(pready && !response.error,"APB access");data=response.data;
    @(negedge pclk);psel=0;penable=0;
  endtask
  task automatic inject(input logic [31:0] address,input logic [12:0] events,input logic [31:0] boot_epoch=7);
    @(negedge cclk);occurrence[0]='0;occurrence[0].boot_epoch=boot_epoch;occurrence[0].config_epoch=23;
    occurrence[0].events=events;occurrence[0].error_valid=events[5] || events[6];
    occurrence[0].error.address={8'hfa,address};occurrence[0].error.axi_id=16'hbeef;
    occurrence[0].error.rank_index=1;occurrence[0].error.operation=LPDDR5_MC_WRITE;
    occurrence[0].error.tag=64'hfedcba9876543210;occurrence[0].error.correlated=1;occurrence[0].error.syndrome=9'h123;
    occurrence_valid=1;@(negedge cclk);occurrence_valid=0;
  endtask
  initial begin
    zero_config.publication='0;zero_config.boot_epoch=0;
    for(int r=0;r<REG_COUNT;r++) begin global_status[r]=0;snapshot[r]=0;zero_config.image[r]=0;end
    snapshot[INDEX_BOOT_EPOCH]=7;snapshot[INDEX_TEMP_MR4]=9;
    global_status[INDEX_BOOT_EPOCH]=7;global_status[INDEX_STATUS]=32'h104;
    #20;preset_n=1;crst_n=1;cold_preset_n=1;cold_crst_n=1;
    wait(p_rearm_ready && c_rearm_ready);
    @(negedge pclk);p_rearm=1;@(negedge pclk);p_rearm=0;
    @(negedge cclk);c_rearm=1;@(negedge cclk);c_rearm=0;
    wait(!pfault && !cfault);
    access(1,18'h1c,32'h20);
    inject(32'h12345678,13'h20);held=notification;
    repeat(8) @(negedge cclk);
    check(nvalid && notification==held,"stalled notification changed");
    @(negedge cclk);destructive=1;@(negedge cclk);destructive=0;
    @(negedge pclk);global_status[INDEX_BOOT_EPOCH]=8;
    check(nvalid && notification==held,"destructive recovery lost occurrence");
    // A queued record and first capture survive fractional operational reset.
    preset_n=0;crst_n=0;#2.125;preset_n=1;crst_n=1;
    repeat(20) @(negedge pclk);
    check(nvalid && notification==held && first_valid && first_version==1,
      "raw common reset lost queued occurrence or first error");
    access(1,18'h1c,32'h20);
    allow_send=1;wait(delivered==1);repeat(10) @(negedge pclk);
    access(0,18'h10258,0);check(data==1 && irq,"retained CE not delivered once");
    access(0,18'h10240,0);check(data==32'h12345678,"retained error detail");
    access(0,18'h10244,0);check(data==32'hfa,"full address");
    access(0,18'h10248,0);check(data==32'hbeef,"full ID");
    access(0,18'h10250,0);check(data==32'hfedcba98,"full tag");
    access(0,18'h10000+18'(REG_OFFSET[INDEX_ERROR_INFO]),0);
    check(data[17:14]==4'(LPDDR5_MC_WRITE) && data[13] && data[9] && data[8:0]==9'h123
      && data[31:18]==0,"rank/operation/error metadata retained across recovery");
    // A delivered diagnostic and count have their own retained APB owner.
    preset_n=0;#2.125;preset_n=1;repeat(20) @(negedge pclk);
    access(0,18'h10240,0);check(data==32'h12345678,"APB reset lost first error");
    access(0,18'h10258,0);check(data==1,"APB reset lost CE count");
    access(1,18'h1c,32'h20);check(irq,"APB reset lost pending event");
    access(0,18'h38,0);check(data==8,"live BOOT_EPOCH changed");
    // Observe the canonical owner directly as well as software reads.
    check(apb.staging_q[0][INDEX_BOOT_EPOCH]==8 && apb.staging_q[0][INDEX_CONFIG_EPOCH]==0,"old event overwrote live epochs");
    snapshot[INDEX_TEMP_MR4]=15;repeat(30) @(negedge pclk);
    access(0,18'h1020c,0);check(data!=15,"stale ordinary snapshot admitted");
    snapshot[INDEX_BOOT_EPOCH]=8;snapshot[INDEX_TEMP_MR4]=9;repeat(30) @(negedge pclk);
    access(0,18'h1020c,0);check(data==9,"fresh snapshot rejected");
    snapshot[INDEX_CONFIG_EPOCH]=99;snapshot[INDEX_TEMP_MR4]=14;repeat(30) @(negedge pclk);
    access(0,18'h1020c,0);check(data==9,"wrong configuration snapshot admitted");
    snapshot[INDEX_CONFIG_EPOCH]=0;snapshot[INDEX_TEMP_MR4]=9;
    access(1,18'h18,32'h20);check(!irq,"W1C did not clear IRQ");
    repeat(20) @(negedge pclk);access(0,18'h10258,0);check(data==1 && !irq,"ordinary snapshot replayed event");
    inject(32'h87654321,13'h20,8);wait(delivered==2);repeat(5) @(negedge pclk);
    access(0,18'h10240,0);check(data==32'h87654321,"later same-bit detail lost");
    access(0,18'h10258,0);check(data==2 && irq,"later same-bit count");
    access(0,18'h1020c,0);check(data==9,"current-boot occurrence overwrote live snapshot");
    allow_send=0;inject(1,13'h40);inject(2,13'h40);inject(3,13'h40);
    check(overflow && notification.error.address[31:0]==1,"overflow replaced retained head");
    held=notification;repeat(5) @(negedge cclk);check(notification==held,"overflow changed stalled head");
    destructive=1;@(negedge cclk);destructive=0;check(!overflow && notification==held,"recovery lost overflow queue");
    allow_send=1;wait(delivered==5);repeat(30) @(negedge pclk);
    access(0,18'h1025c,0);check(data==2,"overflow lost accepted UE records");
    access(0,18'h18,0);check(data[11] && data[9],"reserved overflow report lost");
    check(delivered==5,"event replay");
    @(negedge cclk);check(perf_ready,"performance capture admission");
    snapshot[INDEX_PERF_PAYLOAD_RD_0]=32'h89abcdef;snapshot[INDEX_PERF_PAYLOAD_RD_1]=32'h01234567;
    snapshot[INDEX_PERF_CYCLES_0]=32'h76543210;snapshot[INDEX_PERF_CYCLES_1]=32'hfedcba98;
    perf_capture=1;@(negedge cclk);perf_capture=0;
    snapshot[INDEX_PERF_PAYLOAD_RD_0]=0;snapshot[INDEX_PERF_PAYLOAD_RD_1]=0;
    snapshot[INDEX_PERF_CYCLES_0]=0;snapshot[INDEX_PERF_CYCLES_1]=0;
    repeat(40) @(negedge pclk);
    access(0,18'h10000+18'(REG_OFFSET[INDEX_PERF_PAYLOAD_RD_0]),0);check(data==32'h89abcdef,"captured read counter low");
    access(0,18'h10000+18'(REG_OFFSET[INDEX_PERF_PAYLOAD_RD_1]),0);check(data==32'h01234567,"captured read counter high");
    access(0,18'h10000+18'(REG_OFFSET[INDEX_PERF_CYCLES_0]),0);check(data==32'h76543210,"captured cycles low");
    access(0,18'h10000+18'(REG_OFFSET[INDEX_PERF_CYCLES_1]),0);check(data==32'hfedcba98,"captured cycles high");
    // Stop the destination clock after a coherent event enters the transport.
    // Reset both operational domains while its acknowledgement cannot advance.
    access(1,18'h18,32'h20);
    inject(32'h13579bdf,13'h20,8);
    for(int n=0;n<100;n++) begin
      @(posedge cclk);
      if(bridge_valid && bridge_ready && bridge_status.events[5]) break;
      check(n!=99,"in-flight event did not reach retained mailbox");
    end
    // Freeze after source acceptance: an earlier ordinary snapshot must first
    // leave the single-entry mailbox. The event cannot cross two APB samples yet.
    run_pclk=0;
    @(negedge cclk);preset_n=0;crst_n=0;#2.125;preset_n=1;crst_n=1;
    repeat(12) @(negedge cclk);
    check(delivered==5,"stopped APB clock consumed an occurrence");
    run_pclk=1;wait(delivered==6);repeat(40) @(negedge pclk);
    access(0,18'h10240,0);check(data==32'h13579bdf,"raw reset lost in-flight first error");
    access(0,18'h10258,0);check(data==3,"raw reset lost or replayed in-flight CE");
    access(0,18'h1025c,0);check(data==2,"raw reset lost older UE count");
    access(0,18'h10000+18'(REG_OFFSET[INDEX_PERF_PAYLOAD_RD_0]),0);
    check(data==32'h89abcdef,"raw reset lost explicit performance capture");
    repeat(30) @(negedge pclk);check(delivered==6,"raw reset duplicated occurrence");
    $display("NOTIFICATION_INTEGRATION_PASS checks=%0d delivered=%0d clocks=10ns/7ns",checks,delivered);$finish;
  end
  initial begin #100000;$fatal(1,"notification integration deadline");end
endmodule
