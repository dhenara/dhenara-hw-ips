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
  logic [1:0][31:0] rank_budget,external_age;
  logic [1:0] recovered;
  assign unused_debug=^{update_window,maintenance_enable,command_ready,issue_enable,stop_wck,maintenance_start,sr_enter,sr_exit,external_refresh,extra_service,syscs,refresh_request,first_fault,fault_code,
    rejected,update_request,fsp_request,close_pages,ca,lp_ctrl,lp_data,owner,train_request,boot_epoch};
  lpddr5_mc_lifecycle dut(.clk_ck_i(clk),.rst_ni(rst_n),.cold_rst_ni(cold_rst_n),.profile_i(effective),.fsp_target_profile_i(target_profile),.profile_valid_i(profile_ready),.profile_epoch_i(profile_epoch),
    .command_valid_i(command_valid),.command_i(command),.command_ready_o(command_ready),.command_rejected_o(rejected),
    .host_settled_i(host_settled),.backend_drained_i(backend_drained),.integrity_drained_i(integrity_drained),.pages_closed_i(pages_closed),
    .wck_disabled_i(wck_disabled),.maintenance_urgent_i(urgent),.extra_refresh_i(extra),.refresh_budget_i(budget),.rank_refresh_budget_i(rank_budget),.external_refresh_recovered_i(recovered),.fault_i(fault_input),.reset_confirmed_i(reset_confirmed),
    .train_ack_i(train_ack),.train_token_i(token),.management_i(management),.rank_report_valid_i(reports),.rank_self_refresh_i(rank_sr),.phy_refresh_done_i(phy_refresh_done),
    .update_busy_i(update_busy),.update_done_i(update_done),.update_fault_i(update_fault),.update_request_o(update_request),.update_window_o(update_window),
    .fsp_busy_i(fsp_busy),.fsp_done_i(fsp_done),.fsp_declined_i(fsp_declined),.fsp_fault_i(fsp_fault),.fsp_request_o(fsp_request),
    .maintenance_enable_o(maintenance_enable),.admission_o(admission),.issue_o(issue_enable),.close_o(close_pages),.stop_o(stop_wck),.destructive_reset_o(destructive_reset),.maintenance_start_o(maintenance_start),
    .self_refresh_enter_o(sr_enter),.self_refresh_exit_o(sr_exit),.external_refresh_o(external_refresh),.external_refresh_age_o(external_age),.extra_service_o(extra_service),.ca_o(ca),
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
  int requested_at[2],executed_at[2],done_at[2],executions=0,window_executions=0;
  int pin_cs=0,pin_ca=0;
  bit pin_owner=0;
  string path;
  assign fault_input=maint_fault;
  lpddr5_mc_maintenance maintenance (
    .clk_ck_i(clk),.rst_ni(rst_n),.destructive_reset_i(destructive_reset),.start_i(maintenance_start),
    .pins_idle_i(pages_closed && wck_disabled),.profile_i(profile),.per_bank_i(1'b0),.event_i(event_value),
    .temperature_valid_i(temperature_valid),.temperature_i(temperature),.self_refresh_enter_i(sr_enter),
    .self_refresh_exit_i(sr_exit),.external_refresh_i(external_refresh),.external_refresh_age_i(external_age),.rank_refresh_budget_o(rank_budget),.external_refresh_recovered_o(recovered),.self_refresh_request_i(extra_service),
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
    $fdisplay(stream,"{\"kind\":\"%s\",\"ck\":%0d,\"ack\":%0d,\"request\":%0d,\"done\":%0d,\"external\":%0d,\"phy_request\":%0d,\"reports\":%0d,\"admission\":%0d,\"issue\":%0d,\"settled\":%0d,\"closed\":%0d,\"wck_off\":%0d,\"fault\":%0d,\"code\":%0d,\"budget\":%0d,\"urgent\":%0d,\"thermal\":%0d,\"temperature_valid\":%0d,\"temperature\":%0d,\"mr4_request\":%0d,\"mr4_done\":%0d,\"zq_request\":%0d,\"zq_done\":%0d,\"service\":%0d,\"maint_valid\":%0d,\"maint_ready\":%0d,\"pin_cs\":%0d,\"pin_ca\":%0d,\"pin_owner\":\"%s\",\"state\":%0d,\"master_elapsed\":%0d,\"refresh_age0\":%0d,\"refresh_age1\":%0d,\"last_refresh0\":%0d,\"last_refresh1\":%0d,\"rank_budget0\":%0d,\"rank_budget1\":%0d,\"recovered\":%0d,\"external_age0\":%0d,\"external_age1\":%0d}",
      kind,ck,master_ack,refresh_request,phy_refresh_done,external_refresh,management.phymstr_req,reports,
      admission,issue_enable,host_settled,pages_closed,wck_disabled,fault,fault_code,budget,urgent,thermal,
      temperature_valid,temperature,mr4_request,mr4_done,zq_request,zq_done,maintenance_enable,maint_valid,
      maint_ready,pin_cs,pin_ca,owner_text,state_value,dut.master_elapsed_q,
      dut.refresh_age_q[0],dut.refresh_age_q[1],maintenance.last_refresh_q[0],maintenance.last_refresh_q[1],rank_budget[0],rank_budget[1],recovered,external_age[0],external_age[1]);
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
    if(mode inside {1,2,3,4,5,6,7,9,10,11,12,14,15,16,17,18}) profile.ranks=1;
    management='0;maint_ready=0;ready_at[0]=0;ready_at[1]=0;
    rst_n=0;cold_rst_n=0;#0.001;clk=1;#0.001;clk=0;#1.248;
    rst_n=1;cold_rst_n=1;management.init_complete=0;tick();
    management.init_complete=1;tick();train_ack=1;tick();train_ack=0;
    repeat(4) service_tick();
    assert(trusted && train_done && !fault) else $fatal(1,"boot seam");
    for(int n=0;n<3500;n++) begin
      service_tick();
      if(thermal==12 && !mr4_request && !zq_request && ck>=ready_at[0] && ck>=ready_at[1]) break;
    end
    assert(thermal==12 && !fault && mr4_end<0 && zq_end<0) else $fatal(1,"natural maintenance preparation");
    // The initial hot MR4 publication can already leave a shorter reserve.
    // For asymmetry/type-tail tests first wait for a genuine REF renewal.
    if(mode inside {8,9,13,15,18}) begin
      for(int n=0;n<800;n++) begin service_tick();if(budget>700) break;end
      assert(budget>700) else $fatal(1,"natural renewal missing");
    end
    for(int n=0;n<800;n++) begin
      // 432 = type256 + (registered phase1 + response2 + tRFC168)
      // + five edges from request admission to first REF decision. The
      // natural two-CK rank skew selects only the older rank at that edge.
      if(budget<=(mode inside {8,13,18} ? 432 : mode inside {9,15} ? 500 : 268) && budget>=266 && !urgent && pages_closed) break;
      service_tick();
    end
    assert(budget>=266 && !urgent && !fault) else $fatal(1,"natural reserve phase");
    if(mode==18) begin
      // Explicit external backend-event seam, matched by independent actual
      // DRAM ACT1/2 and PRE CA. No maintenance counter is written. This
      // creates real nonzero paired RAA for the subsequent PHY REF to clear.
      backend_drained=0;pages_closed=0;maint_ready=0;mr4_done=0;zq_done=0;temperature_valid=0;
      pin_cs=1;pin_ca=7;tick();pin_ca=3;event_value.row_opened=1;tick();
      event_value='0;pin_cs=0;pin_ca=0;
      repeat(int'(profile.dram_ck_tras_min)) tick();
      pin_cs=1;pin_ca='h2078;tick();pin_cs=0;pin_ca=0;
      repeat(int'(profile.dram_ck_trp_ab)) tick();
      backend_drained=1;pages_closed=1;
    end
    for(int window=0;window<(mode==8 ? 2 : 1);window++) begin
      for(int r=0;r<2;r++) begin requested_at[r]=-1;executed_at[r]=-1;done_at[r]=-1;end
      window_executions=executions;
      management.phymstr_req=1;management.phymstr_cs_state=3;management.phymstr_state_sel=0;
      if(mode==16) begin
        phy_refresh_done=1;tick();assert(fault && fault_code==7 && !master_ack) else $fatal(1,"unsolicited pre-entry done");
        phy_refresh_done=0;repeat(180) begin tick();assert(fault && !trusted && !admission && !issue_enable) else $fatal(1,"pre-entry fault tail");end
        break;
      end
      for(int n=0;n<int'(profile.management_ck_tphymstr_resp);n++) begin service_tick();if(master_ack) break;end
      assert(master_ack && !fault) else $fatal(1,"master admission");
      reports=0;
      for(int n=0;n<270;n++) begin
        pin_cs=0;pin_ca=0;pin_owner=0;phy_refresh_done=0;maint_ready=0;
        mr4_done=0;zq_done=0;temperature_valid=0;
        if(n==1) reports=3;
        for(int r=0;r<int'(profile.ranks);r++) begin
          if(refresh_request[r] && requested_at[r]<0) requested_at[r]=ck-1;
          if(requested_at[r]>=0 && executed_at[r]<0 && !(mode inside {3,9,15})) begin
            if(ck==requested_at[r]+(mode==4 ? 3 : mode==1 || r==1 ? 2 : 1)) begin
              assert(pin_cs==0 && ck>=ready_at[r]) else $fatal(1,"PHY fixture collision/recovery");
              pin_cs=1<<r;pin_ca='h2038;pin_owner=1;executed_at[r]=ck;executions++;
              ready_at[r]=ck+int'(profile.dram_ck_trfc_ab);
              if(mode!=10) begin phy_refresh_done[r]=1;done_at[r]=ck;end
            end
          end
          if(mode==10 && executed_at[r]>=0 && ck==executed_at[r]+1) begin phy_refresh_done[r]=1;done_at[r]=ck;end
        end
        if(mode==5 && done_at[0]>=0 && ck==done_at[0]+1) phy_refresh_done=1;
        if(mode inside {6,13} && phy_refresh_done!=0) phy_refresh_done=2;
        if(mode==7 && n==0) begin phy_refresh_done=1;#0.1;phy_refresh_done=0;end
        if(mode==15 && n==100) begin temperature_valid=1;temperature=14;end
        if(mode==14 && executed_at[0]>=0 && ck==ready_at[0]-1) management.phymstr_req=0;
        if(mode==2 && executed_at[0]>=0 && ck==executed_at[0]+1) management.phymstr_req=0;
        if(!(mode inside {2,3,4,5,6,9,13,14,15}) && executions>window_executions && ck>=ready_at[0] && ck>=ready_at[1]
            && (done_at[0]<0 || ck>=done_at[0]+int'(profile.dram_ck_trfc_ab))
            && (done_at[1]<0 || ck>=done_at[1]+int'(profile.dram_ck_trfc_ab))) begin
          management.phymstr_req=0;
          if(mode==11) reports=0;
          if(mode==12) rank_sr=1;
        end
        // A second fresh window returns at its first legal sampled edge,
        // while a low rank budget would otherwise request REF on that edge.
        if(mode==8 && window==1 && n==1) management.phymstr_req=0;
        tick();
        if(fault || !master_ack) break;
      end
      if(mode inside {2,3,4,5,6,9,11,12,13,14,15}) begin
        assert(fault && !trusted && master_ack) else $fatal(1,"expected contained master failure mode=%0d",mode);
        assert(fault_code==(mode inside {2,11,12,14} ? 9 : mode inside {3,4} ? 6 : mode==9 ? 8 : mode==15 ? 17 : 7))
          else $fatal(1,"wrong first failure mode=%0d code=%0d",mode,fault_code);
        phy_refresh_done=0;pin_cs=0;pin_ca=0;pin_owner=0;command=CH_CMD_ENABLE;command_valid=1;
        repeat(180) begin tick();assert(fault && !trusted && !admission && !issue_enable && master_ack) else $fatal(1,"unsafe fault tail");end
        command_valid=0;break;
      end
      assert(!fault && !master_ack && refresh_request==0) else $fatal(1,"healthy master return");
      phy_refresh_done=0;pin_cs=0;pin_ca=0;pin_owner=0;
      repeat(3) service_tick();
      if(mode==8 && window==0) begin
        // The unserviced rank can now finish its ordinary queued REF.
        // Drain that real recovery before requesting a subsequent window.
        for(int n=0;n<800;n++) begin
          service_tick();if(pages_closed && !urgent && budget>=266) break;
        end
      end
      assert(!fault && state_value==1) else $fatal(1,"healthy quiet tail");
      if(mode==17) begin
        phy_refresh_done=1;tick();assert(fault && fault_code==7 && !master_ack) else $fatal(1,"stale post-return done");
        phy_refresh_done=0;repeat(180) begin tick();assert(fault && !trusted && !admission && !issue_enable) else $fatal(1,"post-return fault tail");end
      end
    end
    $fclose(stream);stream=0;
    $display("MASTER_REFRESH_SEAM mode=%0d executions=%0d",mode,executions);$finish;
  end
endmodule
