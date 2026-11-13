// Actual lifecycle/configuration coupling; source settlement and PHY reports
// are explicit harness inputs. This seam does not qualify host/pin behavior.
module wrapper_recovery_tb;
  timeunit 1ns; timeprecision 1ps;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  bit clk=0,rst_n=0;
  initial forever #0.625 clk=~clk;
  int stream,tick=-1;
  string path;
  logic command_valid=0,fault_input=0,confirmed=0,reset_request=0,authorized=0,train_ack=0;
  lpddr5_mc_channel_command_t command=CH_CMD_NONE;
  lpddr5_mc_management_in_t [1:0] management;
  lpddr5_mc_channel_config_t [1:0] cfg,active,prepared_image;
  lpddr5_mc_config_mailbox_t [1:0] mailbox_value;
  logic [1:0] destructive,prepared,active_valid,admission,issue_enable,trusted,trained,train_request;
  logic [1:0][31:0] boot,epoch;
  logic [1:0][2:0] state_value;
  lpddr5_mc_ownership_t [1:0] owner;
  for(genvar c=0;c<2;c++) begin : lanes
    logic unused_ready,unused_rejected,unused_error,unused_config_fault;
    logic [31:0] unused_prepared_epoch,unused_first_fault;
    logic [7:0] unused_fault_code;
    logic unused_close,unused_stop,unused_maintenance,unused_maintenance_start,unused_extra;
    logic unused_update,unused_window,unused_fsp,unused_lp_ctrl,unused_lp_data,unused_master,unused_fault;
    logic [1:0] unused_enter,unused_exit,unused_refresh,unused_syscs,unused_refresh_request;
    lpddr5_mc_ca_t unused_ca;
    lpddr5_mc_channel_config #(.CHANNEL_INDEX(c)) configuration (
      .clk_ck_i(clk),.rst_ni(rst_n),.destructive_reset_i(destructive[c]),.discard_prepared_i(1'b0),
      .boot_epoch_i(boot[c]),.drained_i(1'b1),.config_i(cfg[c]),.mailbox_i(mailbox_value[c]),
      .prepared_valid_o(prepared[c]),.prepare_error_o(unused_error),.prepared_epoch_o(unused_prepared_epoch),
      .committed_epoch_o(epoch[c]),.active_valid_o(active_valid[c]),.fault_o(unused_config_fault),
      .prepared_o(prepared_image[c]),.active_o(active[c]));
    lpddr5_mc_lifecycle lifecycle (
      .clk_ck_i(clk),.rst_ni(rst_n),.cold_rst_ni(rst_n),.profile_i(cfg[c].profile),.fsp_target_profile_i(cfg[c].profile),
      .profile_valid_i(active_valid[c]),.profile_epoch_i(epoch[c]),.command_valid_i(command_valid),.command_i(command),
      .command_ready_o(unused_ready),.command_rejected_o(unused_rejected),.host_settled_i(1'b1),
      .backend_drained_i(1'b1),.integrity_drained_i(1'b1),.pages_closed_i(1'b1),.wck_disabled_i(1'b1),
      .maintenance_urgent_i(1'b0),.extra_refresh_i(1'b0),.refresh_budget_i(32'd3000),
      .fault_i(fault_input),.reset_confirmed_i(authorized),.train_ack_i(train_ack),.train_token_i(boot[c]),
      .management_i(management[c]),.rank_report_valid_i(2'b01),.rank_self_refresh_i(2'b00),.phy_refresh_done_i(2'b00),
      .update_busy_i(1'b0),.update_done_i(1'b0),.update_fault_i(1'b0),.update_request_o(unused_update),.update_window_o(unused_window),
      .fsp_busy_i(1'b0),.fsp_done_i(1'b0),.fsp_declined_i(1'b0),.fsp_fault_i(1'b0),.fsp_request_o(unused_fsp),
      .admission_o(admission[c]),.issue_o(issue_enable[c]),.close_o(unused_close),.stop_o(unused_stop),
      .maintenance_enable_o(unused_maintenance),.destructive_reset_o(destructive[c]),.maintenance_start_o(unused_maintenance_start),
      .self_refresh_enter_o(unused_enter),.self_refresh_exit_o(unused_exit),.external_refresh_o(unused_refresh),
      .extra_service_o(unused_extra),.ca_o(unused_ca),.lp_ctrl_request_o(unused_lp_ctrl),.lp_data_request_o(unused_lp_data),
      .master_ack_o(unused_master),.syscs_state_o(unused_syscs),.phy_refresh_request_o(unused_refresh_request),
      .state_o(state_value[c]),.owner_o(owner[c]),.fault_o(unused_fault),.trusted_o(trusted[c]),.train_request_o(train_request[c]),
      .train_done_o(trained[c]),.boot_epoch_o(boot[c]),.first_fault_o(unused_first_fault),.fault_code_o(unused_fault_code));
  end
  function automatic longint timestamp_ps();
    realtime captured;
    captured=$realtime;return longint'(captured*1000.0);
  endfunction
  // Keep the actual invalid payload clearing visible alongside validity.
  task automatic capture(input string kind);
    for(int c=0;c<2;c++)
      $fdisplay(stream,"{\"kind\":\"%s\",\"ps\":%0d,\"tick\":%0d,\"channel\":%0d,\"boot\":%0d,\"config\":%0d,\"config_valid\":%0d,\"prepared_valid\":%0d,\"destructive\":%0d,\"trusted\":%0d,\"trained\":%0d,\"train_request\":%0d,\"training_epoch\":%0d,\"enabled\":%0d,\"admission\":%0d,\"issue\":%0d,\"state\":%0d,\"owner\":%0d,\"confirmed\":%0d,\"reset_request\":%0d,\"reset_authorized\":%0d,\"recover_command\":%0d,\"source_settled\":1,\"active_zero\":%0d,\"prepared_zero\":%0d}",
        kind,timestamp_ps(),tick,c,boot[c],epoch[c],active_valid[c],prepared[c],destructive[c],trusted[c],trained[c],train_request[c],
        management[c].training_epoch,(state_value[c]==2),admission[c],issue_enable[c],state_value[c],owner[c],confirmed,reset_request,authorized,
        (command_valid && command==CH_CMD_RECOVER),(active[c]=='0),(prepared_image[c]=='0));
  endtask
  initial forever begin @(negedge clk);if(stream!=0) begin tick++;capture("sample");end end
  initial forever begin @(posedge clk);if(stream!=0 && tick>=0) capture("management_edge");end
  task automatic step; @(negedge clk);#0.001;endtask
  task automatic publish(input int sequence_value);
    for(int c=0;c<2;c++) begin
      cfg[c]='0;cfg[c].profile=fixture_profile(0,0,0,0,0,0);cfg[c].profile.ranks=1;
      cfg[c].service_div=2;cfg[c].age_tick_div=8;cfg[c].read_urgent_age=32;cfg[c].write_urgent_age=32;
      cfg[c].bw_window=3000;cfg[c].boot_epoch=boot[c];cfg[c].config_epoch=32'(sequence_value);
      mailbox_value[c]='0;mailbox_value[c].destinations=2'b01<<c;mailbox_value[c].epoch=32'(sequence_value);
      mailbox_value[c].payload_crc=config_crc(cfg[c]);mailbox_value[c].prepare=1;
    end
    step();
    for(int c=0;c<2;c++) begin mailbox_value[c].prepare=0;mailbox_value[c].commit=1;end
    step();mailbox_value='0;
    assert(active_valid==3 && prepared==0) else $fatal(1,"actual configuration publication failed");
    for(int c=0;c<2;c++) begin management[c].init_complete=0;management[c].training_epoch=boot[c];end
    step();for(int c=0;c<2;c++) management[c].init_complete=1;
    step();assert(train_request==3) else $fatal(1,"fresh training request");
    train_ack=1;step();train_ack=0;repeat(3) step();
    assert(trusted==3 && trained==3) else $fatal(1,"actual fresh training failed");
  endtask
  initial begin
    management='0;cfg='0;mailbox_value='0;
    assert($value$plusargs("observations=%s",path)) else $fatal(1,"observation path missing");
    stream=$fopen(path,"w");assert(stream!=0) else $fatal(1,"observation open");
    repeat(2) step();rst_n=1;step();publish(1);
    for(int cycle=0;cycle<2;cycle++) begin
      $fdisplay(stream,"{\"kind\":\"begin\",\"ps\":%0d}",timestamp_ps());
      fault_input=1;step();fault_input=0;step();
      reset_request=1;confirmed=0;step();step();confirmed=1;step();
      authorized=1;command=CH_CMD_RECOVER;command_valid=1;step();
      assert(destructive==3 && active_valid==3 && trusted==0 && trained==0) else $fatal(1,"registered reset assertion seam");
      command_valid=0;command=CH_CMD_NONE;reset_request=0;step();authorized=0;
      assert(destructive==0 && active_valid==0 && prepared==0 && active=='0 && prepared_image=='0)
        else $fatal(1,"registered reset consumption invalidation");
      $fdisplay(stream,"{\"kind\":\"baseline\",\"ps\":%0d}",timestamp_ps());
      publish(cycle+2);step();
    end
    // Retain the final rising witness before stopping capture.
    @(posedge clk);#0.001;$fclose(stream);stream=0;
    $display("WRAPPER_RECOVERY_PASS channels=2 cycles=2 actual_registered_reset=1");$finish;
  end
endmodule
