// Owning control tests. Host/channel signals are explicit unit-test witnesses;
// controller-level tests must additionally bind their actual RTL owners.
module coordinator_tb;
  import lpddr5_mc_apb_metadata_pkg::*;
  import lpddr5_mc_apb_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0,rst_n=0;
  always #0.625 clk<=~clk;
  lpddr5_mc_control_command_t request;
  logic valid=0,ready,busy=0,external_fault=0;
  logic [1:0] management_request=0,perf_ready=0;
  logic wake_request=0;
  logic cfg_valid=0,cfg_ready,host_cfg_valid,host_cfg_ready=1,host_cfg_active=0;
  lpddr5_mc_host_pkg::lpddr5_mc_host_config_t config_value,host_config;
  logic settled=1,initialized=0,rearm_ready=1,host_fault=0;
  logic enable,fatal,recover,rearm;
  lpddr5_mc_channel_pkg::lpddr5_mc_channel_status_t [1:0] status_value;
  logic [1:0] command_valid,command_ready=0,command_rejected=0,train_ack,snapshot,reset_confirmed=3,channel_confirmed;
  lpddr5_mc_channel_command_t [1:0] command;
  logic [1:0][31:0] train_token;
  logic reset_request,force_fault,fault,rejected,recovered,profile_ready,trained,train_request,quiesced,trusted;
  logic [31:0] boot,epoch;
  logic [3:0] state_value;
  int checks=0,sequence_value=0;
  logic unused_values;
  assign unused_values=^{host_config,profile_ready,trained,train_request,trusted,state_value};
  lpddr5_mc_coordinator dut (
    .clk_i(clk),.rst_ni(rst_n),.cold_rst_ni(rst_n),.command_valid_i(valid),.command_ready_o(ready),.command_i(request),
    .publication_busy_i(busy),.control_fault_i(1'b0),.external_fault_i(external_fault),.management_request_i(management_request),.wake_request_i(wake_request),
    .configuration_valid_i(cfg_valid),.configuration_ready_o(cfg_ready),.configuration_i(config_value),
    .host_config_valid_o(host_cfg_valid),.host_config_ready_i(host_cfg_ready),.host_config_valid_i(host_cfg_active),.host_config_o(host_config),
    .host_settled_i(settled),.host_initialized_i(initialized),.host_rearm_ready_i(rearm_ready),.host_fault_i(host_fault),
    .host_enable_o(enable),.host_fatal_o(fatal),.host_recover_o(recover),.host_rearm_o(rearm),
    .status_i(status_value),.command_valid_o(command_valid),.command_ready_i(command_ready),.command_rejected_i(command_rejected),.command_o(command),
    .train_ack_o(train_ack),.perf_snapshot_o(snapshot),.perf_ready_i(perf_ready),.train_token_o(train_token),.destructive_reset_request_o(reset_request),
    .destructive_reset_confirmed_i(reset_confirmed),.channel_reset_confirmed_o(channel_confirmed),.force_channel_fault_o(force_fault),
    .fault_o(fault),.rejected_o(rejected),.recovery_complete_o(recovered),.boot_epoch_o(boot),.config_epoch_o(epoch),
    .profile_valid_o(profile_ready),.trained_o(trained),.train_request_o(train_request),.quiesced_o(quiesced),.trusted_o(trusted),.state_o(state_value));
  task automatic tick;
    @(negedge clk);
  endtask
  task automatic check(input logic condition,input string message);
    assert(condition) else $fatal(1,"%s",message);checks++;
  endtask
  task automatic submit(input int index_value,input logic [31:0] mask_value,token=0,boot_value=0);
    check(ready,"coordinator command admission");
    sequence_value++;
    request='{sequence_number:32'(sequence_value),boot_epoch:boot_value,destinations:2'b11,
      register_index:10'(index_value),command_mask:mask_value,write_data:mask_value,training_token:token};
    valid=1;tick();valid=0;
  endtask
  task automatic power_case(input bit self_refresh,decline);
    status_value[0].power_policy=self_refresh ? 2'd2 : 2'd1;
    status_value[0].power_down_idle_ck=0;status_value[0].self_refresh_idle_ck=0;
    repeat(4) begin tick();check(enable && command_valid==0,"zero idle threshold disables power opportunity");end
    status_value[0].power_down_idle_ck=2;status_value[0].self_refresh_idle_ck=2;
    for(int n=0;n<8;n++) begin tick();if(command_valid!=0) break;end
    check(command_valid==1 && !enable && command[0]==(self_refresh ? CH_CMD_SELF_REFRESH : CH_CMD_POWER_DOWN),"committed per-channel power dispatch fences source admission");
    command_ready=1;tick();command_ready=0;status_value[0].state=3;
    repeat(2) begin tick();check(!ready && !enable,"power entry waits on actual channel state");end
    if(decline) begin status_value[0].state=1;tick();end
    else begin
      status_value[0].state=self_refresh ? 4 : 3;command_ready=1;tick();
      repeat(4) begin tick();check(!enable && !fault && command_valid==0,"held power state cannot auto-resume");end
      wake_request=1;tick();
      check(command_valid==1 && command[0]==CH_CMD_WAKE && !enable,"new AXI demand wakes only sleeping channel");
      tick();command_ready=0;status_value[0].state=1;tick();
    end
    status_value[0].power_policy=0;
    tick();check(command_valid==1 && command[0]==CH_CMD_ENABLE && !enable,"resume only quiet destination after wake or normal decline");
    command_ready=1;tick();status_value[0].state=2;tick();
    check(enable && ready && !fault,"fresh RUN witness restores source admission");
    command_ready=0;wake_request=0;
  endtask
  initial begin
    request='0;config_value='0;status_value='0;
    config_value.config_epoch=1;
    for(int c=0;c<2;c++) begin
      status_value[c].state=0;status_value[c].drained=1;
      status_value[c].config_valid=1;status_value[c].config_epoch=1;
    end
    #10;rst_n=1;tick();
    cfg_valid=1;#0.01;check(cfg_ready && host_cfg_valid,"settled initial host image installation");
    tick();cfg_valid=0;host_cfg_active=1;tick();
    check(rearm,"initial host transport rearm");
    tick();check(!rearm,"rearm must pulse once");initialized=1;tick();
    submit(INDEX_CONTROL,1);check(rejected && command_valid==0 && !enable,"untrained ENABLE rejection");tick();
    for(int c=0;c<2;c++) status_value[c].train_request=1;
    tick();submit(INDEX_TRAIN_ACK,1);
    check(train_ack==3 && train_token==0,"matching training token delivered once");tick();
    check(train_ack==0,"training acknowledgement pulse");
    for(int c=0;c<2;c++) begin
      status_value[c].train_request=0;status_value[c].train_done=1;
      status_value[c].memory_trusted=1;status_value[c].state=1;
    end
    tick();submit(INDEX_CONTROL,1);
    check(command_valid==3 && command[0]==CH_CMD_ENABLE && command[1]==CH_CMD_ENABLE && !enable,"enable dispatch before admission");
    command_ready=1;tick();status_value[0].state=2;
    check(command_valid==2 && !enable,"independent command destination acknowledgement");
    repeat(3) begin tick();check(command_valid==2 && !enable,"stalled destination cannot duplicate accepted command");end
    command_ready=2;tick();status_value[1].state=2;tick();
    check(enable && ready,"common enable after both actual RUN witnesses");command_ready=0;
    // Unsolicited PHY work closes admission immediately, before channel state
    // crosses the sampled boundary. Resume still waits on both RUN witnesses.
    management_request=1;#0.01;check(!enable,"PHY request admission fence");
    management_request=0;#0.01;check(enable,"unchanged RUN can continue after absent request");
    power_case(0,0);power_case(1,0);power_case(1,1);
    submit(INDEX_CONTROL,2);
    check(!enable && command_valid==3,"quiesce closes admission before command readiness");
    command_ready=3;settled=0;tick();
    for(int c=0;c<2;c++) status_value[c].state=1;
    repeat(3) begin tick();check(!ready && !quiesced,"channel emptiness cannot replace host settlement");end
    settled=1;tick();check(ready && quiesced,"actual host/channel drain");command_ready=0;
    submit(INDEX_PERF_SNAPSHOT,1);
    check(snapshot==0 && !ready,"performance capture waits for destination capacity");
    perf_ready=1;#0.01;check(snapshot==1,"first destination performance capture");tick();
    check(snapshot==0 && !ready,"captured destination must not replay while peer stalls");
    perf_ready=3;#0.01;check(snapshot==2,"second destination performance capture");tick();
    check(ready && snapshot==0,"performance capture transaction completes once");
    external_fault=1;tick();
    check(fault && fatal && force_fault && !enable,"common fault propagation");
    for(int c=0;c<2;c++) begin status_value[c].fault=1;status_value[c].state=5;status_value[c].memory_trusted=0;end
    external_fault=0;host_fault=1;settled=0;tick();submit(INDEX_CONTROL,8);
    repeat(3) begin tick();check(!reset_request && command_valid==0,"no destructive reset before accepted-host settlement");end
    settled=1;tick();check(reset_request,"settled failure requests destructive reset");
    repeat(3) begin tick();check(reset_request && command_valid==0,"stale high reset acknowledgement cannot authorize recovery");end
    reset_confirmed=0;tick();reset_confirmed=3;tick();
    check(command_valid==3 && channel_confirmed==3 && !fatal,"fresh destructive reset authorizes channel recovery");
    // Confirmation is retained while destination readiness stalls.
    reset_confirmed=0;repeat(3) begin tick();check(command_valid==3 && channel_confirmed==3,"retained reset proof");end
    command_ready=3;tick();
    for(int c=0;c<2;c++) begin
      status_value[c]='0;status_value[c].boot_epoch=1;status_value[c].drained=1;
    end
    tick();check(recover,"new boot epochs allow host recovery");host_fault=0;initialized=0;host_cfg_active=0;
    tick();check(recovered && !fault && boot==1 && epoch==0 && !enable,"recovery invalidates old service");tick();
    submit(INDEX_TRAIN_ACK,1,0,0);check(rejected && train_ack==0,"old boot training command rejected");tick();
    submit(INDEX_CONTROL,1,0,1);check(rejected && !enable,"recovery needs fresh profile and training before enable");
    $display("COORDINATOR_INTEGRATION_PASS checks=%0d",checks);$finish;
  end
  initial begin #10000;$fatal(1,"coordinator deadline");end
endmodule
