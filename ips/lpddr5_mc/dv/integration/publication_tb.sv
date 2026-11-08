// Real publication adapter and channel/FSP engines. Empty host traffic is an
// explicit fixture condition; datapath/recovery tests own nonempty host drain.
module publication_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_apb_metadata_pkg::*;
  import lpddr5_mc_apb_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  import lpddr5_mc_integration_pkg::*;
  logic clk=0,rst_n=0;
  always #0.625 clk<=~clk;
  lpddr5_mc_profile_t profile;
  lpddr5_mc_profile_t [2:0] qualified;
  lpddr5_mc_configuration_payload_t [1:0] payload;
  logic [1:0] valid_value=0,ready_value,ack_valid,ack_ready=0,adapter_fault;
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
    lpddr5_mc_channel_control #(.CHANNEL_INDEX(c)) adapter (
      .clk_i(clk),.rst_ni(rst_n),.valid_i(valid_value[c]),.ready_o(ready_value[c]),.payload_i(payload[c]),.qualified_profiles_i(qualified),
      .ack_valid_o(ack_valid[c]),.ack_ready_i(ack_ready[c]),.ack_o(ack[c]),.config_o(cfg[c]),.mailbox_o(mailbox_value[c]),
      .prepared_valid_i(prepared[c]),.prepare_error_i(prepare_error[c]),.fsp_ready_i(fsp_ready[c]),.fsp_declined_i(fsp_declined[c]),
      .prepared_epoch_i(prepared_epoch[c]),.committed_epoch_i(committed_epoch[c]),.status_i(status_value[c]),
      .fsp_command_valid_o(fsp_command[c]),.command_ready_i(command_ready[c]),.command_rejected_i(rejected[c]),
      .target_fsp_o(target_fsp[c]),.target_frequency_o(frequency[c]),.fault_o(adapter_fault[c]));
    lpddr5_mc_channel #(.DEPTH(16),.CHANNEL_INDEX(c)) channel (
      .clk_ck_i(clk),.rst_ni(rst_n),.cold_rst_ni(rst_n),.force_fault_i(1'b0),.request_valid_i(1'b0),.request_ready_o(request_ready),.request_i(lpddr5_mc_granule_t'('0)),
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
  task automatic transfer(input logic [1:0] destinations);
    @(negedge clk);check((ready_value & destinations)==destinations,"adapter readiness");valid_value=destinations;
    @(negedge clk);valid_value=0;
  endtask
  task automatic receive(input logic [1:0] destinations,input lpddr5_mc_publication_phase_t phase_value,input logic accepted_value);
    for(int n=0;n<4000;n++) begin
      @(negedge clk);
      if((ack_valid & destinations)==destinations) begin
        for(int c=0;c<2;c++) if(destinations[c]) check(ack[c].phase==phase_value && ack[c].accepted==accepted_value
          && ack[c].sequence_number==payload[c].publication.epoch && ack[c].boot_epoch==0,"exact publication acknowledgement");
        ack_ready=destinations;@(negedge clk);ack_ready=0;return;
      end
    end
    $fatal(1,"publication ACK deadline stage%0d state=%0d/%0d fault=%b",stage,channels[0].adapter.state_q,channels[1].adapter.state_q,adapter_fault);
  endtask
  task automatic prepare_image(input logic [31:0] epoch_value,input logic [1:0] bank_value);
    for(int c=0;c<2;c++) begin
      payload[c].boot_epoch=0;payload[c].image[INDEX_DVFS]={30'd0,bank_value};
      payload[c].publication='0;payload[c].publication.prepare=1;payload[c].publication.destinations=3;
      payload[c].publication.epoch=epoch_value;payload[c].publication.payload_crc=image_crc(payload[c].image);
    end
    transfer(3);
  endtask
  task automatic commit_image;
    for(int c=0;c<2;c++) begin payload[c].publication.prepare=0;payload[c].publication.commit=1;end
    transfer(3);receive(3,LPDDR5_MC_CONFIG_COMMIT,1);
    check(&{status_value[0].config_valid,status_value[1].config_valid},"common publication validity");
    check(committed_epoch[0]==payload[0].publication.epoch && committed_epoch[1]==payload[1].publication.epoch,"common publication epoch");
  endtask
  initial begin
    string path;
    int stream,count,number,ranks,target;
    logic [31:0] word_value;
    for(int c=0;c<2;c++) begin
      payload[c].publication='0;payload[c].boot_epoch=0;
      for(int r=0;r<REG_COUNT;r++) payload[c].image[r]=0;
    end
    qualified='0;command='0;management='0;
    check($value$plusargs("images=%s",path),"missing image vectors");stream=$fopen(path,"r");
    check(stream!=0 && $fscanf(stream,"%d",count)==1 && count==192,"image header");
    for(int f=0;f<3;f++) begin
      check($fscanf(stream,"%d %d %d",number,ranks,target)==3 && number==0 && ranks==1 && target==f,"image identity");
      for(int r=0;r<REG_COUNT;r++) begin
        check($fscanf(stream,"%h",word_value)==1,"image word");
        if(f==0 || REG_FSP[r]==f) for(int c=0;c<2;c++) payload[c].image[r]=word_value;
      end
    end
    for(int c=0;c<2;c++) payload[c].image[INDEX_FSP_VALID]=7;
    $fclose(stream);expected_profile(0);profile.ranks=1;for(int f=0;f<3;f++) qualified[f]=profile;
    #20;rst_n=1;memory_reset=0;repeat(4) @(negedge clk);
    prepare_image(1,0);receive(3,LPDDR5_MC_CONFIG_PREPARE,1);commit_image();stage=1;
    for(int c=0;c<2;c++) management[c].init_complete=1;
    wait(status_value[0].train_request && status_value[1].train_request);
    @(negedge clk);train_ack=3;@(negedge clk);train_ack=0;
    wait(status_value[0].memory_trusted && status_value[1].memory_trusted);stage=2;
    // Selecting a different bank with an identical profile still requires FSP.
    // Unchanged high init_complete is a normal decline at each real engine.
    prepare_image(2,1);receive(3,LPDDR5_MC_CONFIG_DECLINE,0);
    check(committed_epoch[0]==1 && committed_epoch[1]==1 && status_value[0].config_valid && status_value[1].config_valid
      && adapter_fault==0 && prepared==0,"unanimous decline changed active image");stage=3;
    prepare_image(3,1);
    wait(management_out[0].init_start && management_out[1].init_start);
    @(negedge clk);for(int c=0;c<2;c++) management[c].init_complete=0;
    // The target is held by the adapter and real engine during PHY training.
    wait(!management_out[0].init_start && !management_out[1].init_start);
    repeat(3) @(negedge clk);
    for(int c=0;c<2;c++) management[c].init_complete=1;
    receive(3,LPDDR5_MC_CONFIG_PREPARE,1);
    check(prepared==3 && fsp_ready==3 && committed_epoch[0]==1 && committed_epoch[1]==1,"prepare committed early");
    commit_image();stage=4;
    check(adapter_fault==0 && !status_value[0].fault && !status_value[1].fault,"clean publication fault");
    $display("PUBLICATION_INTEGRATION_PASS checks=%0d stages=%0d",checks,stage);$finish;
  end
  initial begin #20000;$fatal(1,"publication deadline stage%0d",stage);end
endmodule
