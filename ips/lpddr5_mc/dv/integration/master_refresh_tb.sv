module master_refresh_tb;
  timeunit 1ns; timeprecision 1ps;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0,rst_n=0,cold_rst_n=0,profile_ready=1,command_valid=0,command_ready,rejected;
  logic host_settled=1,backend_drained=1,integrity_drained=1,pages_closed=1,wck_disabled=1,urgent,extra;
  logic fault_input,reset_confirmed=0,train_ack=0;
  logic [31:0] profile_epoch=1,budget,token=0;
  lpddr5_mc_profile_t profile,target_profile,effective;
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
  assign unused_debug=^{update_window,maintenance_enable,command_ready,issue_enable,stop_wck,maintenance_start,sr_enter,sr_exit,external_refresh,extra_service,syscs,refresh_request,first_fault,fault_code,
    rejected,update_request,fsp_request,close_pages,ca,lp_ctrl,lp_data,owner,train_request,boot_epoch};
  lpddr5_mc_lifecycle dut(.clk_ck_i(clk),.rst_ni(rst_n),.cold_rst_ni(cold_rst_n),.profile_i(effective),.fsp_target_profile_i(target_profile),.profile_valid_i(profile_ready),.profile_epoch_i(profile_epoch),
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
  // The absent host/backend/register owners are explicit timed seam inputs.
  // No hierarchy is written. All budget/urgency/timer state comes from RTL.
  logic maint_valid,maint_ready,maint_rank,maint_rfm,maint_all,maint_fault,per_bank;
  logic [2:0] maint_pair;
  logic [1:0][7:0] activate_allowed;
  logic mr4_request,zq_request,mr4_done=0,zq_done=0,temperature_valid=0;
  logic [4:0] temperature=12,thermal;
  logic [31:0] diagnostic;
  lpddr5_mc_event_t event_value='0;
  int ck=0,stream,mode=0,ready_at[2],mr4_end=-1,zq_end=-1;
  int pin_cs=0,pin_ca=0;
  bit pin_owner=0;
  string path;
  assign fault_input=maint_fault;
  lpddr5_mc_maintenance maintenance (
    .clk_ck_i(clk),.rst_ni(rst_n),.destructive_reset_i(destructive_reset),.start_i(maintenance_start),
    .pins_idle_i(pages_closed && wck_disabled),.profile_i(profile),.per_bank_i(1'b0),.event_i(event_value),
    .temperature_valid_i(temperature_valid),.temperature_i(temperature),.self_refresh_enter_i(sr_enter),
    .self_refresh_exit_i(sr_exit),.external_refresh_i(external_refresh),.self_refresh_request_i(extra_service),
    .request_valid_o(maint_valid),.request_ready_i(maint_ready),.request_rank_o(maint_rank),
    .request_rfm_o(maint_rfm),.request_all_o(maint_all),.request_pair_o(maint_pair),
    .activate_allowed_o(activate_allowed),.urgent_o(urgent),.fault_o(maint_fault),.extra_pending_o(extra),
    .refresh_budget_o(budget),.effective_per_bank_o(per_bank),.effective_profile_o(effective),
    .mr4_request_o(mr4_request),.zq_request_o(zq_request),.mr4_done_i(mr4_done),.zq_done_i(zq_done),
    .thermal_o(thermal),.diagnostic_o(diagnostic));
  logic unused_maintenance;
  assign unused_maintenance=^{activate_allowed,per_bank,thermal,diagnostic,maint_pair};
  task automatic capture(input string kind);
    string owner_text;
    owner_text="controller";if(pin_owner) owner_text="phy";
    $fdisplay(stream,"{\"kind\":\"%s\",\"ck\":%0d,\"ack\":%0d,\"request\":%0d,\"done\":%0d,\"external\":%0d,\"phy_request\":%0d,\"reports\":%0d,\"admission\":%0d,\"issue\":%0d,\"settled\":%0d,\"closed\":%0d,\"wck_off\":%0d,\"fault\":%0d,\"code\":%0d,\"budget\":%0d,\"urgent\":%0d,\"thermal\":%0d,\"temperature_valid\":%0d,\"temperature\":%0d,\"mr4_request\":%0d,\"mr4_done\":%0d,\"zq_request\":%0d,\"zq_done\":%0d,\"service\":%0d,\"maint_valid\":%0d,\"maint_ready\":%0d,\"pin_cs\":%0d,\"pin_ca\":%0d,\"pin_owner\":\"%s\",\"state\":%0d,\"master_elapsed\":%0d,\"refresh_age0\":%0d,\"refresh_age1\":%0d,\"last_refresh0\":%0d,\"last_refresh1\":%0d}",
      kind,ck,master_ack,refresh_request,phy_refresh_done,external_refresh,management.phymstr_req,reports,
      admission,issue_enable,host_settled,pages_closed,wck_disabled,fault,fault_code,budget,urgent,thermal,
      temperature_valid,temperature,mr4_request,mr4_done,zq_request,zq_done,maintenance_enable,maint_valid,
      maint_ready,pin_cs,pin_ca,owner_text,state_value,dut.master_elapsed_q,
      dut.refresh_age_q[0],dut.refresh_age_q[1],maintenance.last_refresh_q[0],maintenance.last_refresh_q[1]);
  endtask
  task automatic tick;
    #0.001;capture("edge");clk=1;#0.001;capture("sample");clk=0;#1.248;ck++;
  endtask
  // Pin fixture: accepts only genuine pending maintenance, enforcing its own
  // per-rank tRFC/tRFM. MR4/ZQ completion are declared register-seam inputs,
  // bounded below by full recovery/service; they are not MRR pin evidence.
  task automatic service_tick;
    maint_ready=0;mr4_done=0;zq_done=0;temperature_valid=0;
    pin_cs=0;pin_ca=0;pin_owner=0;
    backend_drained=ck>=ready_at[0] && ck>=ready_at[1] && mr4_end<0 && zq_end<0;
    pages_closed=backend_drained;
    if(maintenance_enable && maint_valid && ck>=ready_at[int'(maint_rank)]) begin
      maint_ready=1;pin_cs=1<<int'(maint_rank);
      pin_ca=int'(ca_maintenance(0,maint_rfm,maint_all,{1'b0,maint_pair}));
      ready_at[int'(maint_rank)]=ck+(maint_rfm ? int'(profile.dram_ck_trfm_ab) : int'(profile.dram_ck_trfc_ab));
    end
    if(maintenance_enable && !urgent && ck>=ready_at[0] && ck>=ready_at[1]) begin
      if(mr4_request && mr4_end<0) mr4_end=ck+int'(profile.ranks)*int'(periodic_register_budget(profile));
      else if(!mr4_request && zq_request && zq_end<0) zq_end=ck+int'(profile.ranks)*(int'(profile.dram_ck_tzqlat)+2);
    end
    if(ck==mr4_end) begin mr4_done=1;temperature_valid=1;mr4_end=-1;end
    if(ck==zq_end) begin zq_done=1;zq_end=-1;end
    tick();
  endtask
  initial begin
    assert($value$plusargs("observations=%s",path)) else $fatal(1,"observation path required");
    if($value$plusargs("mode=%d",mode)) begin end
    stream=$fopen(path,"w");assert(stream!=0) else $fatal(1,"observation open");
    profile=fixture_profile(0,0,0,0,0,0);target_profile=profile;
    if(mode==1) profile.ranks=1;
    management='0;maint_ready=0;ready_at[0]=0;ready_at[1]=0;
    // Capture only settled reset-release edges so independent time is monotonic.
    rst_n=0;cold_rst_n=0;#0.001;clk=1;#0.001;clk=0;#1.248;
    rst_n=1;cold_rst_n=1;management.init_complete=0;tick();
    management.init_complete=1;tick();train_ack=1;tick();train_ack=0;
    repeat(4) service_tick();
    assert(trusted && train_done && !fault) else $fatal(1,"boot seam");
    // Let the actual timer request natural REF/MR4/ZQ. No done is driven
    // without its own request, and no hidden timer/budget state is assigned.
    for(int n=0;n<3500;n++) begin
      service_tick();
      if(thermal==12 && !mr4_request && !zq_request && ck>=ready_at[0] && ck>=ready_at[1]) break;
    end
    assert(thermal==12 && !fault && mr4_end<0 && zq_end<0) else $fatal(1,"natural maintenance preparation");
    for(int n=0;n<800;n++) begin
      if(budget<=268 && budget>=266 && !urgent && pages_closed) break;
      service_tick();
    end
    assert(budget>=266 && budget<=268 && !urgent && !fault) else $fatal(1,"natural reserve phase");
    management.phymstr_req=1;management.phymstr_cs_state=3;management.phymstr_state_sel=0;
    for(int n=0;n<12;n++) begin service_tick();if(master_ack) break;end
    assert(master_ack && !fault && budget>=256) else $fatal(1,"master admission");
    reports=0;service_tick();reports=3;
    for(int n=0;n<257;n++) begin
      pin_cs=0;pin_ca=0;pin_owner=0;phy_refresh_done=0;maint_ready=0;
      mr4_done=0;zq_done=0;temperature_valid=0;
      // A repeated request cannot authorize REF before the preceding tRFC.
      if(refresh_request[0] && ck>=ready_at[0]) begin
        phy_refresh_done=1;pin_cs=1;pin_ca='h2038;pin_owner=1;ready_at[0]=ck+int'(profile.dram_ck_trfc_ab);
      end else if(refresh_request[1] && ck>=ready_at[1]) begin
        phy_refresh_done=2;pin_cs=2;pin_ca='h2038;pin_owner=1;ready_at[1]=ck+int'(profile.dram_ck_trfc_ab);
      end
      if(mode==1 && external_refresh!=0) management.phymstr_req=0;
      tick();
      if(fault || (!master_ack && mode==1)) break;
    end
    phy_refresh_done=0;pin_cs=0;pin_ca=0;pin_owner=0;
    if(mode==0) begin
      assert(fault && fault_code==8 && master_ack) else $fatal(1,"expected type/recovery conflict");
      repeat(172) tick();
    end else begin
      assert(!fault && !master_ack) else $fatal(1,"early-return counterexample");
      repeat(3) tick();command=CH_CMD_ENABLE;command_valid=1;tick();command_valid=0;tick();
      assert(admission && issue_enable) else $fatal(1,"seam early service observation");
    end
    $fclose(stream);stream=0;
    $display("MASTER_REFRESH_SEAM mode=%0d expected_counterexample=1",mode);$finish;
  end
endmodule
