module lpddr5_mc_lifecycle_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0,rst_n=0,cold_rst_n=0,profile_ready=1,command_valid=0,command_ready,rejected;
  logic host_settled=1,backend_drained=1,integrity_drained=1,pages_closed=1,wck_disabled=1,urgent=0,extra=0;
  logic fault_input=0,reset_confirmed=0,train_ack=0;
  logic [31:0] profile_epoch=1,budget=3000,token=0;
  lpddr5_mc_profile_t profile;
  lpddr5_mc_channel_command_t command=CH_CMD_NONE;
  lpddr5_mc_management_in_t management;
  logic [1:0] reports=3,rank_sr=0,phy_refresh_done=0;
  logic update_busy=0,update_done=0,update_fault=0,update_request,update_window;
  logic fsp_busy=0,fsp_done=0,fsp_declined=0,fsp_fault=0,fsp_request;
  logic admission,issue_enable,close_pages,stop_wck,destructive_reset,maintenance_start,extra_service;
  logic [1:0] sr_enter,sr_exit,external_refresh,syscs,refresh_request;
  lpddr5_mc_ca_t ca;
  logic lp_ctrl,lp_data,master_ack;
  logic [2:0] state_value;
  lpddr5_mc_ownership_t owner;
  logic fault,trusted,train_request,train_done;
  logic [31:0] boot_epoch,first_fault;
  logic [7:0] fault_code;
  logic unused_debug, maintenance_enable;
  assign unused_debug=^{update_window,maintenance_enable,command_ready,issue_enable,stop_wck,maintenance_start,sr_enter,sr_exit,external_refresh,extra_service,syscs,refresh_request,first_fault,fault_code};
  lpddr5_mc_lifecycle dut(.clk_ck_i(clk),.rst_ni(rst_n),.cold_rst_ni(cold_rst_n),.profile_i(profile),.profile_valid_i(profile_ready),.profile_epoch_i(profile_epoch),
    .command_valid_i(command_valid),.command_i(command),.command_ready_o(command_ready),.command_rejected_o(rejected),
    .host_settled_i(host_settled),.backend_drained_i(backend_drained),.integrity_drained_i(integrity_drained),.pages_closed_i(pages_closed),
    .wck_disabled_i(wck_disabled),.maintenance_urgent_i(urgent),.extra_refresh_i(extra),.refresh_budget_i(budget),.fault_i(fault_input),.reset_confirmed_i(reset_confirmed),
    .train_ack_i(train_ack),.train_token_i(token),.management_i(management),.rank_report_valid_i(reports),.rank_self_refresh_i(rank_sr),.phy_refresh_done_i(phy_refresh_done),
    .update_busy_i(update_busy),.update_done_i(update_done),.update_fault_i(update_fault),.update_request_o(update_request),.update_window_o(update_window),
    .fsp_busy_i(fsp_busy),.fsp_done_i(fsp_done),.fsp_declined_i(fsp_declined),.fsp_fault_i(fsp_fault),.fsp_request_o(fsp_request),
    .maintenance_enable_o(maintenance_enable),.admission_o(admission),.issue_o(issue_enable),.close_o(close_pages),.stop_o(stop_wck),.destructive_reset_o(destructive_reset),.maintenance_start_o(maintenance_start),
    .self_refresh_enter_o(sr_enter),.self_refresh_exit_o(sr_exit),.external_refresh_o(external_refresh),.extra_service_o(extra_service),.ca_o(ca),
    .lp_ctrl_request_o(lp_ctrl),.lp_data_request_o(lp_data),.master_ack_o(master_ack),.syscs_state_o(syscs),.phy_refresh_request_o(refresh_request),
    .state_o(state_value),.owner_o(owner),.fault_o(fault),.trusted_o(trusted),.train_request_o(train_request),.train_done_o(train_done),
    .boot_epoch_o(boot_epoch),.first_fault_o(first_fault),.fault_code_o(fault_code));
  task automatic tick; #1; clk=1; #1; clk=0; #1; endtask
  task automatic reset;
    rst_n=0;cold_rst_n=0; tick(); rst_n=1;cold_rst_n=1; management='0; management.init_complete=1; reports=3; rank_sr=0;
    command_valid=0; profile_ready=1; profile_epoch=1; backend_drained=1; host_settled=1; integrity_drained=1; pages_closed=1; wck_disabled=1;
    train_ack=0; token=0; fault_input=0; reset_confirmed=0; urgent=0; extra=0; budget=3000; update_busy=0; update_done=0; phy_refresh_done=0;
  endtask
  task automatic send(input lpddr5_mc_channel_command_t value);
    command=value; command_valid=1; tick(); command_valid=0;
  endtask
  task automatic boot;
    management.init_complete=0; tick(); management.init_complete=1; tick();
    assert(train_request && !trusted) else $fatal(1,"fresh boot training request");
    train_ack=1; token=boot_epoch; tick(); train_ack=0; repeat(3) tick();
    assert(train_done && trusted && state_value==1 && !admission) else $fatal(1,"boot handover");
  endtask
  task automatic wait_lp;
    for(int i=0;i<20;i++) begin
      if(lp_ctrl && lp_data && !fault) return;
      tick();
    end
    $fatal(1,"LP request");
  endtask
  task automatic assert_fault;
    tick(); assert(fault && !trusted && !admission && owner==LPDDR5_MC_OWN_FAULT) else $fatal(1,"missing lifecycle fault");
  endtask
  initial begin
    logic [31:0] diagnostic;
    profile=fixture_profile(0,0,0,0,0,0);
    reset(); repeat(8) tick(); assert(!train_request && !train_done && !trusted) else $fatal(1,"stale high boot");
    train_ack=1; token=0; tick(); train_ack=0; assert(rejected && !train_done) else $fatal(1,"unrequested token");
    boot(); send(CH_CMD_ENABLE); assert(admission) else $fatal(1,"enable");
    // Quiesce cannot use an empty command queue as a host handshake.
    host_settled=0; send(CH_CMD_QUIESCE); repeat(5) tick();
    assert(admission && issue_enable && close_pages && state_value==3) else $fatal(1,"accepted CDC work must enter during drain");
    host_settled=1; #1; assert(!admission) else $fatal(1,"settled drain ingress fence");
    tick(); assert(state_value==1 && !admission) else $fatal(1,"quiesce");
    send(CH_CMD_POWER_DOWN); wait_lp(); repeat(32) tick();
    assert(state_value==1 && !lp_ctrl && !fault) else $fatal(1,"normal LP decline");
    send(CH_CMD_POWER_DOWN);wait_lp();management.lp_ctrl_ack=1;management.lp_data_ack=1;tick();
    assert(ca.cs==3 && ca.address[6:0]==64) else $fatal(1,"power-down entry encoding");
    tick();
    assert(!command_ready) else $fatal(1,"WAKE acknowledged before minimum low-power residency");
    repeat(12) tick();send(CH_CMD_WAKE);
    assert(!lp_ctrl && !lp_data && !fault) else $fatal(1,"power-down wake request release");
    management.lp_ctrl_ack=0;management.lp_data_ack=0;tick();
    assert(ca.cs==3 && ca.address==0 && !update_request) else $fatal(1,"power-down exit ownership");
    repeat(3) begin assert(ca.cs==3 && ca.address==0) else $fatal(1,"PDX high pulse width");tick();end
    repeat(4) begin assert(ca.cs==0 && ca.address==0) else $fatal(1,"PDX low pulse width");tick();end
    repeat(10) tick();assert(state_value==1 && trusted && !fault) else $fatal(1,"power-down tXP return");
    send(CH_CMD_SELF_REFRESH); wait_lp(); management.lp_ctrl_ack=1; management.lp_data_ack=1; tick();
    assert(ca.cs==3 && ca.address[6:0]==104) else $fatal(1,"SRE encoding");
    tick(); repeat(30) tick(); assert(state_value==4 && !fault) else $fatal(1,"self refresh hold");
    send(CH_CMD_WAKE); management.lp_ctrl_ack=0; management.lp_data_ack=0; repeat(2) tick();
    assert(update_request && update_window && ca.cs==0) else $fatal(1,"update before SRX");
    update_busy=1; tick(); update_busy=0; update_done=1; tick(); update_done=0;
    assert(ca.cs==3 && ca.address[6:0]==40) else $fatal(1,"SRX encoding");
    tick(); repeat(175) tick(); assert(state_value==1 && !fault && trusted) else $fatal(1,"SRX recovery");
    send(CH_CMD_FSP); repeat(3) tick(); assert(fsp_request && !fault) else $fatal(1,"FSP admission");
    fsp_busy=1; tick(); assert(!fsp_request) else $fatal(1,"FSP issued once"); fsp_busy=0; fsp_done=1; tick(); fsp_done=0;
    assert(state_value==1 && trusted) else $fatal(1,"FSP publication return");
    budget=fsp_admission_budget(profile)+1;send(CH_CMD_FSP);repeat(4) tick();
    assert(maintenance_enable && !fsp_request && !fault) else $fatal(1,"FSP reserve stranded maintenance");
    budget=fsp_admission_budget(profile)+2;tick();assert(fsp_request && !maintenance_enable) else $fatal(1,"FSP reserve boundary");
    fsp_busy=1;tick();fsp_busy=0;fsp_declined=1;tick();fsp_declined=0;
    budget=update_admission_budget(profile,0,0)+1;send(CH_CMD_UPDATE);repeat(4) tick();
    assert(maintenance_enable && !update_request && !update_window && !fault) else $fatal(1,"update reserve stranded maintenance");
    budget=update_admission_budget(profile,0,0)+2;tick();assert(update_request && !maintenance_enable) else $fatal(1,"update reserve boundary");
    update_busy=1;tick();update_busy=0;update_done=1;tick();update_done=0;budget=3000;
    // Lost ownership requires real host settlement and confirmed reset.
    fault_input=1; assert_fault(); fault_input=0; diagnostic=first_fault;
    host_settled=0; reset_confirmed=1; send(CH_CMD_RECOVER); assert(rejected && fault && boot_epoch==0) else $fatal(1,"unsettled destructive recovery");
    host_settled=1; send(CH_CMD_RECOVER); assert(destructive_reset && boot_epoch==1 && !trusted && !train_done) else $fatal(1,"destructive recovery");
    management.training_epoch=1; management.init_complete=0; tick(); management.init_complete=1; repeat(3) tick();
    assert(!train_request && first_fault==diagnostic) else $fatal(1,"retired profile fence/diagnostic");
    profile_epoch=2; tick(); train_ack=1; token=0; tick(); train_ack=0;
    assert(rejected && !train_done) else $fatal(1,"stale training token");
    train_ack=1; token=1; tick(); train_ack=0; repeat(3) tick();
    assert(trusted && train_done && state_value==1 && first_fault==diagnostic) else $fatal(1,"fresh recovery boot");
    // All ranks must return with freshly reported, unchanged chosen states.
    host_settled=0;management.phymstr_req=1; management.phymstr_type=0; management.phymstr_cs_state=3; management.phymstr_state_sel=1;
    repeat(3) tick();assert(admission && issue_enable && !master_ack && state_value==3) else $fatal(1,"pending master stranded accepted host work");
    host_settled=1;repeat(7) tick(); assert(master_ack && !admission && !fault && owner==LPDDR5_MC_OWN_PHY) else $fatal(1,"master entry");
    reports=0; tick();reports=3;rank_sr=0;
    budget=32'(profile.management_ck_tphymstr_rfsh)+8;tick();
    assert(refresh_request==3 && ca.cs==0 && master_ack && !fault) else $fatal(1,"master IDLE refresh request");
    // The admitted PHY profile requires execution within two CK. Complete
    // each rank on a distinct in-bound edge; a later case omits completion.
    phy_refresh_done=1;tick();
    assert(external_refresh==1 && refresh_request==2 && master_ack) else $fatal(1,"rank-zero actual refresh execution");
    budget=3000;phy_refresh_done=2;tick();
    assert(external_refresh==2 && refresh_request==0 && master_ack) else $fatal(1,"rank-one actual refresh execution");
    phy_refresh_done=0;budget=3000;management.phymstr_req=0; tick(); repeat(2) tick();
    assert(!master_ack && !fault && state_value==1 && ca.cs==0) else $fatal(1,"known IDLE master return");
    management.phymstr_req=1; repeat(7) tick(); assert(master_ack) else $fatal(1,"second master entry");
    management.phymstr_req=0; assert_fault(); assert(master_ack) else $fatal(1,"fault fabricated ownership return");
    // Unsolicited or partially acknowledged low power never resumes traffic.
    reset(); boot(); management.lp_ctrl_ack=1; assert_fault();
    reset(); boot(); send(CH_CMD_POWER_DOWN); wait_lp(); management.lp_ctrl_ack=1; repeat(34) tick();
    assert(fault && lp_ctrl && lp_data) else $fatal(1,"partial LP timeout");
    reset();boot();management.phymstr_req=1;management.phymstr_cs_state=3;management.phymstr_state_sel=1;
    repeat(7) tick();assert(master_ack) else $fatal(1,"refresh timeout master entry");
    budget=32'(profile.management_ck_tphymstr_rfsh)+8;tick();
    assert(refresh_request==3) else $fatal(1,"missing refresh timeout stimulus");
    repeat(int'(profile.management_ck_tphymstr_rfsh)+2) tick();
    assert(fault && !trusted && master_ack && fault_code==6) else $fatal(1,"master missing execution attestation did not fault");
    diagnostic=first_fault;
    assert(diagnostic!=0) else $fatal(1,"first fault was never captured");
    rst_n=0;tick();rst_n=1;profile_ready=0;management='0;fault_input=0;
    repeat(3) tick();assert(first_fault==diagnostic && boot_epoch==0 && !trusted)
      else $fatal(1,"raw reset lost first-fault custody");
    fault_input=1;repeat(2) tick();fault_input=0;
    reset_confirmed=1;send(CH_CMD_RECOVER);
    assert(boot_epoch==1 && first_fault==diagnostic && !trusted) else $fatal(1,"raw-reset recovery diagnostics");
    rst_n=0;tick();rst_n=1;repeat(2) tick();
    assert(boot_epoch==1 && first_fault==diagnostic) else $fatal(1,"second raw reset reused identity or lost diagnostics");
    reset();tick();assert(boot_epoch==0 && first_fault==0) else $fatal(1,"cold reset failed to clear retained state");
    $display("PASS lifecycle: fresh boot/token/epoch fencing, true host drain, LP decline/SR update/wakeup, master return attestation, retained faults and destructive fresh recovery"); $finish;
  end
endmodule
