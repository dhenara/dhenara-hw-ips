// Actual APB coordinator, asynchronous publication links and two channel/FSP
// engines. Host settlement is an explicit empty-host fixture condition.
module apb_publication_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_apb_metadata_pkg::*;
  import lpddr5_mc_apb_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  import lpddr5_mc_integration_pkg::*;
  localparam int PUBLICATION_TIMEOUT=1000;
  logic clk=0,rst_n=0;
  always #0.625 clk<=~clk;
  lpddr5_mc_profile_t profile;
  lpddr5_mc_profile_t [2:0] qualified;
  lpddr5_mc_configuration_payload_t [1:0] payload;
  logic [1:0] valid_value,ready_value,ack_valid,ack_ready,adapter_fault;
  lpddr5_mc_publication_ack_t [1:0] ack;
  lpddr5_mc_channel_config_t [1:0] cfg;
  lpddr5_mc_config_mailbox_t [1:0] mailbox_value;
  logic [1:0] prepared,prepare_error,fsp_ready,fsp_declined,fsp_command,command_ready,rejected;
  logic [1:0][31:0] prepared_epoch,committed_epoch;
  logic [1:0][1:0] target_fsp;
  logic [1:0][4:0] frequency;
  lpddr5_mc_channel_pkg::lpddr5_mc_channel_status_t [1:0] status_value;
  lpddr5_mc_management_in_t [1:0] management;
  lpddr5_mc_management_out_t [1:0] management_out;
  logic [1:0] train_ack=0;
  logic [1:0][31:0] train_token=0;
  lpddr5_mc_ca_t [1:0] ca;
  lpddr5_mc_wck4_t [1:0] wck;
  lpddr5_mc_write_bus4_t [1:0] writes;
  lpddr5_mc_read_request4_t [1:0] reads;
  lpddr5_mc_return4_t [1:0] returns;
  logic [1:0] command_valid=0;
  lpddr5_mc_channel_command_t [1:0] command;
  logic memory_reset=1;
  int checks=0,stage=0;
  logic pclk=0;
  always #3.5 pclk<=~pclk;
  logic psel=0,penable=0,pready,irq,apb_valid,apb_fault,busy;
  lpddr5_mc_apb_request_t apb_request='0;
  lpddr5_mc_apb_response_t apb_response;
  lpddr5_mc_register_image_t global_status;
  lpddr5_mc_register_image_t [1:0] staging_image,config_image,active_image;
  lpddr5_mc_config_mailbox_t [1:0] publication;
  lpddr5_mc_configuration_payload_t [1:0] publication_payload;
  logic [1:0] publication_valid,publication_ready,a_ack_valid,a_ack_ready;
  lpddr5_mc_publication_ack_t [1:0] a_ack,observed_ack;
  logic [1:0] p_fault,c_fault,p_rearm_ready,c_rearm_ready,allow_ack=3;
  logic p_rearm=0,c_rearm=0,inject_stale=0;
  logic [31:0] apb_epoch,config_boot,prior_epoch;
  logic command_pending,global_ready;
  logic [2:0] protection;
  lpddr5_mc_control_command_t queued_command;
  logic [1:0] unused_status_ready;
  lpddr5_mc_apb_pkg::lpddr5_mc_channel_status_t [1:0] zero_status;
  logic unused_apb;
  assign unused_apb=^{irq,command_pending,global_ready,protection,queued_command,unused_status_ready};
  always_comb begin
    observed_ack=a_ack;
    if(inject_stale) observed_ack[0].sequence_number=prior_epoch;
  end
  lpddr5_mc_apb #(.PUBLICATION_TIMEOUT(PUBLICATION_TIMEOUT)) apb (
    .pclk,.preset_n(rst_n),.psel_i(psel),.penable_i(penable),.request_i(apb_request),.pready_o(pready),.response_o(apb_response),
    .enabled_i(1'b0),.quiesced_i(1'b1),.host_settled_i(1'b1),.active_fsp_i(active_image[0][INDEX_DVFS][1:0]),
    .link_fault_i(1'b0),.recovery_confirmed_i(1'b0),.global_status_valid_i(1'b1),.global_status_i(global_status),.global_status_ready_o(global_ready),
    .status_valid_i(2'b0),.status_i(zero_status),.status_ready_o(unused_status_ready),
    .config_valid_o(publication_valid),.config_ready_i(publication_ready),.config_o(publication),.config_image_o(config_image),.config_boot_epoch_o(config_boot),
    .config_ack_valid_i(a_ack_valid & allow_ack),.config_ack_i(observed_ack),.config_ack_ready_o(a_ack_ready),
    .command_valid_o(command_pending),.command_ready_i(1'b1),.command_o(queued_command),.last_protection_o(protection),
    .irq_o(irq),.configuration_valid_o(apb_valid),.fault_o(apb_fault),.publication_busy_o(busy),.configuration_epoch_o(apb_epoch),.active_image_o(active_image));
  for(genvar c=0;c<2;c++) begin : channels
    logic completion_valid,request_ready,notification_valid,notification_error_valid,first_valid;
    logic [12:0] events;
    lpddr5_mc_error_t error_value;
    lpddr5_mc_channel_completion_t completion;
    logic [31:0] notification_boot,notification_epoch,first_boot,first_epoch;
    logic [63:0] first_version;
    int mr43_count,mr4_count,zq_count,refresh_count,rfm_count;
    logic unused_values;
    assign unused_values=^{completion_valid,request_ready,notification_valid,notification_error_valid,first_valid,events,
      error_value,completion,notification_boot,notification_epoch,first_boot,first_epoch,first_version,
      mr43_count,mr4_count,zq_count,refresh_count,rfm_count,wck[c],management_out[c]};
    logic unused_a_status_valid,unused_c_status_ready;
    lpddr5_mc_apb_pkg::lpddr5_mc_channel_status_t unused_a_status;
    logic unused_link;
    assign unused_link=^{unused_a_status_valid,unused_a_status,unused_c_status_ready};
    assign publication_payload[c].publication=publication[c];
    assign publication_payload[c].boot_epoch=config_boot;
    assign publication_payload[c].image=config_image[c];
    lpddr5_mc_control_link crossing (
      .aclk_i(pclk),.arst_ni(rst_n),.a_enable_i(1'b1),.a_rearm_i(p_rearm),.a_fault_o(p_fault[c]),.a_rearm_ready_o(p_rearm_ready[c]),
      .cclk_i(clk),.crst_ni(rst_n),.c_enable_i(1'b1),.c_rearm_i(c_rearm),.c_fault_o(c_fault[c]),.c_rearm_ready_o(c_rearm_ready[c]),
      .a_config_valid_i(publication_valid[c]),.a_config_ready_o(publication_ready[c]),.a_config_i(publication_payload[c]),
      .c_config_valid_o(valid_value[c]),.c_config_ready_i(ready_value[c]),.c_config_o(payload[c]),
      .c_ack_valid_i(ack_valid[c]),.c_ack_ready_o(ack_ready[c]),.c_ack_i(ack[c]),
      .a_ack_valid_o(a_ack_valid[c]),.a_ack_ready_i(a_ack_ready[c] && allow_ack[c]),.a_ack_o(a_ack[c]),
      .c_status_valid_i(1'b0),.c_status_ready_o(unused_c_status_ready),.c_status_i(zero_status[c]),
      .a_status_valid_o(unused_a_status_valid),.a_status_ready_i(1'b1),.a_status_o(unused_a_status));
    lpddr5_mc_channel_control #(.CHANNEL_INDEX(c)) adapter (
      .clk_i(clk),.rst_ni(rst_n),.valid_i(valid_value[c]),.ready_o(ready_value[c]),.payload_i(payload[c]),.qualified_profiles_i(qualified),
      .ack_valid_o(ack_valid[c]),.ack_ready_i(ack_ready[c]),.ack_o(ack[c]),.config_o(cfg[c]),.mailbox_o(mailbox_value[c]),
      .prepared_valid_i(prepared[c]),.prepare_error_i(prepare_error[c]),.fsp_ready_i(fsp_ready[c]),.fsp_declined_i(fsp_declined[c]),
      .prepared_epoch_i(prepared_epoch[c]),.committed_epoch_i(committed_epoch[c]),.status_i(status_value[c]),
      .fsp_command_valid_o(fsp_command[c]),.command_ready_i(command_ready[c]),.command_rejected_i(rejected[c]),
      .target_fsp_o(target_fsp[c]),.target_frequency_o(frequency[c]),.fault_o(adapter_fault[c]));
    lpddr5_mc_channel #(.DEPTH(16),.CHANNEL_INDEX(c)) channel (
      .clk_ck_i(clk),.rst_ni(rst_n),.force_fault_i(1'b0),.request_valid_i(1'b0),.request_ready_o(request_ready),.request_i(lpddr5_mc_granule_t'('0)),
      .request_boot_epoch_i(32'd0),.request_config_epoch_i(32'd0),.completion_valid_o(completion_valid),.completion_ready_i(1'b1),.completion_o(completion),
      .feedback_valid_i(1'b0),.feedback_i(lpddr5_mc_channel_response_feedback_t'('0)),.host_settled_i(1'b1),.reset_confirmed_i(1'b0),
      .command_valid_i(fsp_command[c] || command_valid[c]),.command_i(fsp_command[c] ? CH_CMD_FSP : command[c]),.command_ready_o(command_ready[c]),.command_rejected_o(rejected[c]),
      .train_ack_i(train_ack[c]),.train_token_i(train_token[c]),.config_i(cfg[c]),.mailbox_i(mailbox_value[c]),
      .prepared_valid_o(prepared[c]),.prepare_error_o(prepare_error[c]),.prepared_epoch_o(prepared_epoch[c]),.committed_epoch_o(committed_epoch[c]),
      .fsp_ready_o(fsp_ready[c]),.fsp_declined_o(fsp_declined[c]),.target_fsp_i(target_fsp[c]),.target_frequency_i(frequency[c]),
      .notification_valid_o(notification_valid),.notification_ready_i(1'b1),.notification_events_o(events),.notification_error_valid_o(notification_error_valid),.notification_error_o(error_value),
      .notification_boot_epoch_o(notification_boot),.notification_config_epoch_o(notification_epoch),.first_error_clear_i(1'b0),.first_error_clear_version_i(64'd0),
      .first_error_valid_o(first_valid),.first_error_version_o(first_version),.first_error_boot_epoch_o(first_boot),.first_error_config_epoch_o(first_epoch),
      .management_i(management[c]),.rank_report_valid_i(2'b01),.rank_self_refresh_i(2'b00),.phy_refresh_done_i(2'b00),
      .management_o(management_out[c]),.ca_o(ca[c]),.wck_o(wck[c]),.write_o(writes[c]),.read_request_o(reads[c]),.return_i(returns[c]),.status_o(status_value[c]));
    lpddr5_mc_channel_memory memory_model (
      .profile_i(qualified[0]),.ca_i(ca[c]),.write_i(writes[c]),.read_i(reads[c]),.return_o(returns[c]),.mr43_i(8'd0),.mr4_i(5'd9),
      .mr43_count_o(mr43_count),.mr4_count_o(mr4_count),.zq_count_o(zq_count),.refresh_count_o(refresh_count),.rfm_count_o(rfm_count));
    initial forever begin
      @(negedge clk);
      if(memory_reset) channels[c].memory_model.reset();
      else begin channels[c].memory_model.prepare();channels[c].memory_model.sample();end
    end
  end
  `include "expected_profiles.svh"
  task automatic check(input logic condition,input string message);
    assert(condition) else $fatal(1,"stage%0d: %s",stage,message);checks++;
  endtask
  task automatic access(input bit write_value,input logic [17:0] address_value,input logic [31:0] value,input bit error_value=0);
    @(negedge pclk);psel=1;penable=0;
    apb_request='{address:address_value,write:write_value,data:value,strobe:write_value ? 4'hf : 4'd0,protection:3'd0};
    @(negedge pclk);penable=1;#0.1;
    check(pready && apb_response.error==error_value,"APB access/error disposition");
    @(negedge pclk);psel=0;penable=0;
  endtask
  task automatic wait_publication(input bit expected_fault);
    // A missing ACK can exhaust prepare and then the independently bounded
    // abort. Both phases must settle before judging the final disposition.
    for(int n=0;n<2*PUBLICATION_TIMEOUT+100;n++) begin
      @(negedge pclk);
      if(!busy) begin
        check(apb_fault==expected_fault,"publication fault disposition");return;
      end
    end
    $fatal(1,"APB publication deadline");
  endtask
  task automatic change_bank(input logic [1:0] bank_value);
    access(1,18'(REG_OFFSET[INDEX_DVFS]),{30'd0,bank_value});
    access(1,18'(REG_OFFSET[INDEX_CONFIG_COMMIT]),1);
    check(busy,"accepted CONFIG_COMMIT must begin publication");
  endtask
  initial begin
    string path;
    int stream,count,number,ranks,target,scenario;
    logic [31:0] word_value;
    scenario=0;if($value$plusargs("scenario=%d",scenario)) begin end
    check(scenario inside {0,1,2,3},"scenario selection");
    qualified='0;command='0;management='0;
    for(int r=0;r<REG_COUNT;r++) begin
      global_status[r]=0;
      for(int c=0;c<2;c++) zero_status[c].image[r]=0;
    end
    for(int c=0;c<2;c++) begin zero_status[c].events=0;zero_status[c].error_valid=0;zero_status[c].perf_capture=0;end
    global_status[INDEX_STATUS]=4;
    check($value$plusargs("images=%s",path),"missing image vectors");stream=$fopen(path,"r");
    check(stream!=0 && $fscanf(stream,"%d",count)==1 && count==192,"image header");
    for(int f=0;f<3;f++) begin
      check($fscanf(stream,"%d %d %d",number,ranks,target)==3 && number==0 && ranks==1 && target==f,"image identity");
      for(int r=0;r<REG_COUNT;r++) begin
        check($fscanf(stream,"%h",word_value)==1,"image word");
        if(f==0 || REG_FSP[r]==f) for(int c=0;c<2;c++) staging_image[c][r]=word_value;
      end
    end
    $fclose(stream);
    for(int c=0;c<2;c++) staging_image[c][INDEX_FSP_VALID]=7;
    expected_profile(0);profile.ranks=1;for(int f=0;f<3;f++) qualified[f]=profile;
    #20;rst_n=1;memory_reset=0;
    wait((&p_rearm_ready) && (&c_rearm_ready));
    @(negedge pclk);p_rearm=1;@(negedge pclk);p_rearm=0;
    @(negedge clk);c_rearm=1;@(negedge clk);c_rearm=0;
    wait(p_fault==0 && c_fault==0);
    // Stage every writable register through actual APB transfers. W1S fields
    // remain zero here; CONFIG_COMMIT is the explicit publication trigger.
    for(int r=0;r<REG_COUNT;r++) if(REG_RW[r]!=0) begin
      if(REG_SHARED[r]) access(1,18'(REG_OFFSET[r]),staging_image[0][r] & REG_RW[r]);
      else for(int c=0;c<2;c++) access(1,18'h10000+18'(c)*18'h10000+18'(REG_OFFSET[r]),staging_image[c][r] & REG_RW[r]);
    end
    access(1,18'(REG_OFFSET[INDEX_CONFIG_COMMIT]),1);
    check(busy,"initial CONFIG_COMMIT must begin publication");wait_publication(0);
    check(apb_valid && apb_epoch!=0 && committed_epoch[0]==apb_epoch && committed_epoch[1]==apb_epoch,
      $sformatf("initial common publication valid=%0d epoch=%0d committed=%0d/%0d prepared=%b adapterfault=%b channelstate=%0d/%0d configvalid=%0d/%0d",apb_valid,apb_epoch,committed_epoch[0],committed_epoch[1],prepared,adapter_fault,status_value[0].state,status_value[1].state,status_value[0].config_valid,status_value[1].config_valid));
    stage=1;prior_epoch=apb_epoch;
    for(int c=0;c<2;c++) management[c].init_complete=1;
    for(int n=0;n<100;n++) begin @(negedge clk);if(status_value[0].train_request && status_value[1].train_request) break;end
    check(status_value[0].train_request && status_value[1].train_request,"real channel training requests");
    @(negedge clk);train_ack=3;@(negedge clk);train_ack=0;
    for(int n=0;n<100;n++) begin @(negedge clk);if(status_value[0].memory_trusted && status_value[1].memory_trusted) break;end
    check(status_value[0].memory_trusted && status_value[1].memory_trusted,"real channel training handover");
    change_bank(1);wait_publication(0);
    check(apb_valid && apb_epoch==prior_epoch && committed_epoch[0]==prior_epoch && committed_epoch[1]==prior_epoch,"unanimous decline must retain prior epoch");
    stage=2;
    if(scenario==3) allow_ack=1;
    if(scenario==2) inject_stale=1;
    // GO uses the same all-destination publication owner, including a
    // simultaneous target write. Its W1S bit never remains in the image.
    access(1,18'(REG_OFFSET[INDEX_DVFS]),32'h101);
    check(busy,"DVFS.GO did not enter atomic publication");
    if(scenario==0 || scenario==1) begin
      for(int n=0;n<6500;n++) begin @(negedge clk);if(management_out[0].init_start && management_out[1].init_start) break;end
      check(management_out[0].init_start && management_out[1].init_start,"real simultaneous FSP requests");
      @(negedge clk);management[1].init_complete=0;
      if(scenario==0) management[0].init_complete=0;
      // A later software target mutation is rejected while the frozen image
      // continues toward its original target at both actual engines.
      access(1,18'(REG_OFFSET[INDEX_DVFS]),2,1);
      check(target_fsp==4'b0101,"locked write changed frozen target");
      for(int n=0;n<1500;n++) begin @(negedge clk);if(!management_out[0].init_start && !management_out[1].init_start) break;end
      check(!management_out[0].init_start && !management_out[1].init_start,"FSP registered release");
      repeat(3) @(negedge clk);for(int c=0;c<2;c++) management[c].init_complete=1;
    end
    wait_publication(scenario!=0);
    if(scenario==0) begin
      check(apb_valid && apb_epoch>prior_epoch && committed_epoch[0]==apb_epoch && committed_epoch[1]==apb_epoch,"actual common publication after readiness");
      check(active_image[0][INDEX_DVFS]==1 && active_image[1][INDEX_DVFS]==1,"atomic active image");
    end else check(!apb_valid,"failed publication retained validity");
    check(p_fault==0 && c_fault==0,"control transport fault");
    if(scenario==0) check(adapter_fault==0,"publication adapter fault");
    $display("APB_PUBLICATION_INTEGRATION_PASS scenario=%0d checks=%0d",scenario,checks);$finish;
  end
  initial begin #100000;$fatal(1,"APB publication fixture deadline stage%0d",stage);end
endmodule
