module master_refresh_channel_tb;
  timeunit 1ns;timeprecision 1ps;
  localparam bit PER_BANK_REFRESH=0;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0,rst_n=1,request_valid=0,request_ready,completion_valid,completion_ready=1;
  logic host_settled=1,reset_confirmed=0,command_valid=0,command_ready,command_rejected,train_ack=0;
  logic prepared,prepare_error,fsp_ready,fsp_declined;
  logic [31:0] prepared_epoch,committed_epoch,request_boot=0,request_epoch=1,train_token=0;
  logic [1:0] target_fsp=1,report_valid=3,report_sr=0,phy_refresh_done=0;
  logic [4:0] target_frequency=0;
  logic [7:0] mr43=0;
  logic [4:0] mr4=9;

  lpddr5_mc_granule_t request;
  lpddr5_mc_channel_completion_t completion;
  lpddr5_mc_channel_command_t command=CH_CMD_NONE;
  lpddr5_mc_channel_config_t cfg;
  lpddr5_mc_profile_t memory_profile;
  lpddr5_mc_config_mailbox_t config_mailbox;
  lpddr5_mc_channel_status_t status_value;
  lpddr5_mc_management_in_t management_in;
  lpddr5_mc_management_out_t management_out;
  lpddr5_mc_ca_t ca;
  lpddr5_mc_wck4_t wck;
  lpddr5_mc_write_bus4_t write_value;
  lpddr5_mc_read_request4_t read_value;
  lpddr5_mc_return4_t return_value;
  int mr43_count,mr4_count,zq_count,refresh_count,rfm_count;
  logic notification_valid,notification_error_valid,first_error_valid;
  logic notification_ready=1;
  logic [12:0] notification_events;
  logic [31:0] notification_boot,notification_epoch,first_error_boot,first_error_epoch;
  logic [63:0] first_error_version;
  lpddr5_mc_error_t notification_error;
  logic unused_debug;
  assign unused_debug=^{request_ready,completion_valid,completion,mr43_count,refresh_count,rfm_count,notification_valid,notification_error_valid,notification_events,notification_boot,notification_epoch,notification_error,first_error_valid,first_error_version,first_error_boot,first_error_epoch,status_value,command_rejected,fsp_ready,fsp_declined,wck,prepared_epoch,committed_epoch,management_out};
  lpddr5_mc_channel #(.DEPTH(16),.PER_BANK_REFRESH(PER_BANK_REFRESH)) dut (
    .clk_ck_i(clk),.rst_ni(rst_n),.cold_rst_ni(rst_n),.force_fault_i(1'b0),.request_valid_i(request_valid),.request_ready_o(request_ready),.request_i(request),.request_boot_epoch_i(request_boot),.request_config_epoch_i(request_epoch),
    .completion_valid_o(completion_valid),.completion_ready_i(completion_ready),.completion_o(completion),
    .feedback_valid_i(1'b0),.feedback_i(lpddr5_mc_channel_response_feedback_t'('0)),.host_settled_i(host_settled),.reset_confirmed_i(reset_confirmed),
    .command_valid_i(command_valid),.command_i(command),.command_ready_o(command_ready),.command_rejected_o(command_rejected),.train_ack_i(train_ack),.train_token_i(train_token),
    .config_i(cfg),.mailbox_i(config_mailbox),.prepared_valid_o(prepared),.prepare_error_o(prepare_error),.prepared_epoch_o(prepared_epoch),.committed_epoch_o(committed_epoch),.fsp_ready_o(fsp_ready),.fsp_declined_o(fsp_declined),
    .target_fsp_i(target_fsp),.target_frequency_i(target_frequency),.notification_ready_i(notification_ready),.notification_valid_o(notification_valid),.notification_events_o(notification_events),.notification_error_valid_o(notification_error_valid),.notification_error_o(notification_error),
    .notification_boot_epoch_o(notification_boot),.notification_config_epoch_o(notification_epoch),.first_error_clear_i(1'b0),.first_error_clear_version_i(64'd0),
    .first_error_valid_o(first_error_valid),.first_error_version_o(first_error_version),.first_error_boot_epoch_o(first_error_boot),.first_error_config_epoch_o(first_error_epoch),.management_i(management_in),
    .rank_report_valid_i(report_valid),.rank_self_refresh_i(report_sr),.phy_refresh_done_i(phy_refresh_done),.management_o(management_out),
    .ca_o(ca),.wck_o(wck),.write_o(write_value),.read_request_o(read_value),.return_i(return_value),.status_o(status_value));
  lpddr5_mc_channel_memory memory_model(.profile_i(memory_profile),.ca_i(ca),.write_i(write_value),.read_i(read_value),.return_o(return_value),
    .mr43_i(mr43),.mr4_i(mr4),.mr43_count_o(mr43_count),.mr4_count_o(mr4_count),.zq_count_o(zq_count),.refresh_count_o(refresh_count),.rfm_count_o(rfm_count));
  int ck=0,stream,mode=0,entry_at=-1,ack_at=-1,request_at[2],physical_at[2],ready_at[2];
  int pin_cs=0,pin_ca=0;
  bit pin_owner=0;
  lpddr5_mc_ca_t ca_delay[2];
  string path;
  task automatic capture(input string kind);
    string owner_text;
    owner_text=pin_owner ? "phy" : "controller";
    $fdisplay(stream,"{\"kind\":\"%s\",\"ck\":%0d,\"ack\":%0d,\"request\":%0d,\"done\":%0d,\"external\":%0d,\"phy_request\":%0d,\"reports\":%0d,\"admission\":%0d,\"issue\":%0d,\"settled\":%0d,\"closed\":%0d,\"wck_off\":%0d,\"fault\":%0d,\"code\":%0d,\"budget\":%0d,\"thermal\":%0d,\"temperature_valid\":%0d,\"temperature\":%0d,\"mr4_request\":%0d,\"mr4_done\":%0d,\"zq_request\":%0d,\"zq_done\":%0d,\"pin_cs\":%0d,\"pin_ca\":%0d,\"pin_owner\":\"%s\",\"dfi_cs\":%0d,\"dfi_ca\":%0d,\"return_valid\":%0d,\"register_state\":%0d,\"register_result\":%0d,\"register_value\":%0d,\"backend_drained\":%0d,\"pin_drained\":%0d,\"register_pending\":%0d,\"register_valid\":%0d,\"register_ready\":%0d,\"register_accept\":%0d,\"register_owner\":%0d,\"register_busy\":%0d,\"service\":%0d,\"life_state\":%0d,\"trusted\":%0d,\"recovered\":%0d,\"last_refresh0\":%0d,\"last_refresh1\":%0d,\"external_age0\":%0d,\"external_age1\":%0d}",
      kind,ck,management_out.phymstr_ack,management_out.refresh_request,phy_refresh_done,dut.external_refresh,
      management_in.phymstr_req,report_valid,dut.admission,dut.issue_enable,host_settled,
      dut.pages_closed,dut.wck_disabled,status_value.fault,status_value.fault_code,dut.refresh_budget,
      dut.thermal,dut.temperature_valid,dut.temperature,dut.mr4_request,dut.mr4_done,dut.zq_request,dut.zq_done,
      pin_cs,pin_ca,owner_text,ca.cs,ca.address,return_value.valid,dut.register_state_q,
      dut.register_result_valid,dut.register_result_value,dut.backend_drained,dut.pin_drained,
      dut.register_pending,dut.register_valid,dut.register_ready,dut.backend.register_accept,
      dut.backend.register_owner_q,dut.backend.register_busy,dut.service,dut.lifecycle.state_q,status_value.memory_trusted,dut.external_refresh_recovered,
      dut.maintenance.last_refresh_q[0],dut.maintenance.last_refresh_q[1],dut.external_refresh_age[0],dut.external_refresh_age[1]);
  endtask
  task automatic tick;
    #0.001;
    memory_model.prepare();
    if(!pin_owner) begin pin_cs=int'(ca_delay[1].cs);pin_ca=int'(ca_delay[1].address);end
    ca_delay[1]=ca_delay[0];ca_delay[0]=ca;
    capture("edge");memory_model.sample();clk=1;#0.001;capture("sample");clk=0;#1.248;ck++;
    assert(mode==5 || !status_value.fault) else $fatal(1,"channel fault ck=%0d code=%0d maint=%h",ck,status_value.fault_code,dut.maintenance_diagnostic);
  endtask
  task automatic send_command(input lpddr5_mc_channel_command_t value);
    assert(command_ready) else $fatal(1,"channel command not ready");
    command=value;command_valid=1;tick();command_valid=0;
  endtask
  initial begin
    assert($value$plusargs("observations=%s",path)) else $fatal(1,"observations required");
    if($value$plusargs("mode=%d",mode)) begin end
    stream=$fopen(path,"w");assert(stream!=0) else $fatal(1,"open stream");
    cfg='0;cfg.profile=fixture_profile(0,0,0,0,0,0);cfg.profile.ranks=mode==1 ? 2 : 1;
    cfg.service_div=2;cfg.age_tick_div=8;cfg.read_urgent_age=4;cfg.write_urgent_age=4;
    cfg.bw_window=64;cfg.read_weight=1;cfg.write_weight=1;cfg.config_epoch=1;
    memory_profile=cfg.profile;management_in='0;config_mailbox='0;request='0;mr4=12;
    report_valid=mode==1 ? 3 : 1;ca_delay[0]='0;ca_delay[1]='0;
    for(int r=0;r<2;r++) begin request_at[r]=-1;physical_at[r]=-1;ready_at[r]=0;end
    rst_n=0;#0.001;clk=1;#0.001;clk=0;memory_model.reset();#1.248;rst_n=1;
    config_mailbox.destinations=1;config_mailbox.epoch=1;config_mailbox.payload_crc=config_crc(cfg);config_mailbox.prepare=1;
    tick();config_mailbox.prepare=0;assert(prepared && !prepare_error) else $fatal(1,"prepare");
    config_mailbox.commit=1;tick();config_mailbox='0;
    management_in.init_complete=0;tick();management_in.init_complete=1;repeat(3) tick();
    assert(status_value.train_request) else $fatal(1,"training request");train_ack=1;tick();train_ack=0;
    for(int n=0;n<32;n++) begin tick();if(status_value.state==1) break;end
    assert(status_value.memory_trusted && status_value.train_done) else $fatal(1,"training trust");
    // Real CA MRR, returned data, register result, and hottest-rank reduction.
    // Mode2 requests on the registered thermal-publication phase, before
    // the real pending ZQ owner has completed its drain/service sequence.
    if(mode==5) begin
      // Request at the real MR4 acceptance. Its actual return/WCK tail and
      // already-due ZQ cannot be bypassed to satisfy a master response.
      for(int n=0;n<3500;n++) begin
        if(dut.backend.register_accept && !dut.register_zq_q) break;
        tick();
      end
      assert(dut.backend.register_accept && !dut.register_zq_q && !status_value.fault) else $fatal(1,"natural MR4 acceptance");
    end else begin
      for(int n=0;n<3500;n++) begin tick();if(dut.temperature_valid) break;end
      assert(dut.temperature_valid && dut.temperature==12 && mr4_count==int'(cfg.profile.ranks)) else $fatal(1,"natural MR4 report");
      if(mode==3 || mode==4) begin
        for(int n=0;n<100;n++) begin
          if(mode==3 && dut.backend.register_accept && dut.register_zq_q) break;
          if(mode==4 && dut.register_result_valid && dut.register_result_zq) break;
          tick();
        end
        if(mode==3) assert(dut.backend.register_accept && dut.register_zq_q) else $fatal(1,"natural ZQ acceptance");
        if(mode==4) assert(dut.register_result_valid && dut.register_result_zq) else $fatal(1,"natural ZQ completion");
      end
    end
    // At this actual-channel MR4 phase, the remaining rank budget can be
    // larger than a full master window. Let real ZQ service finish and wait
    // for a risky window naturally; never alter the timer or thermal report.
    if(mode<2) begin
    for(int n=0;n<800;n++) begin
      if(dut.refresh_budget<=432 && dut.refresh_budget>=266 && !dut.maintenance_urgent && dut.pages_closed && !dut.zq_request) break;
      tick();
    end
    assert(dut.refresh_budget<=432 && dut.refresh_budget>=266 && !dut.maintenance_urgent && dut.pages_closed) else $fatal(1,"natural active window");
    end
    management_in.phymstr_req=1;management_in.phymstr_cs_state=3;entry_at=ck;
    for(int n=0;n<int'(cfg.profile.management_ck_tphymstr_resp)+2;n++) begin tick();if(management_out.phymstr_ack || status_value.fault) break;end
    if(mode==5) begin
      // The unchanged 96CK bound may expire with an accepted register visit
      // still in flight. It must fail closed and never grant PHY ownership.
      assert(status_value.fault && status_value.fault_code==12 && !management_out.phymstr_ack && !status_value.memory_trusted)
        else $fatal(1,"entry timeout must fault without ACK ck=%0d request=%0d fault=%0d code=%0d",ck,entry_at,status_value.fault,status_value.fault_code);
      repeat(180) begin tick();assert(status_value.fault && !management_out.phymstr_ack && !dut.admission && !dut.issue_enable && !status_value.memory_trusted) else $fatal(1,"persistent entry failure containment");end
      $fclose(stream);$display("MASTER_REFRESH_CHANNEL mode=5 contained entry timeout request=%0d final=%0d",entry_at,ck);$finish;
    end
    assert(management_out.phymstr_ack) else $fatal(1,"natural channel entry gate budget=%0d urgent=%0d closed=%0d backend=%0d reg=%0d",dut.refresh_budget,dut.maintenance_urgent,dut.pages_closed,dut.backend_drained,dut.register_state_q);
    ack_at=ck-1;report_valid=0;
    for(int n=0;n<256;n++) begin
      pin_cs=0;pin_ca=0;pin_owner=0;phy_refresh_done=0;
      if(n==1) report_valid=mode==1 ? 3 : 1;
      for(int r=0;r<int'(cfg.profile.ranks);r++) begin
        if(management_out.refresh_request[r] && request_at[r]<0) request_at[r]=ck-1;
        if(request_at[r]>=0 && physical_at[r]<0 && ck==request_at[r]+r+1) begin
          assert(ca_delay[1].cs==0 && pin_cs==0) else $fatal(1,"physical CA ownership collision");
          pin_cs=1<<r;pin_ca='h2038;pin_owner=1;phy_refresh_done[r]=1;physical_at[r]=ck;
          ready_at[r]=ck+int'(cfg.profile.dram_ck_trfc_ab);
        end
      end
      if(physical_at[0]>=0 && (mode!=1 || physical_at[1]>=0) && ck>=ready_at[0] && ck>=ready_at[1]) management_in.phymstr_req=0;
      if(mode>=2 && n==16) management_in.phymstr_req=0;
      tick();if(!management_out.phymstr_ack) break;
    end
    assert(!management_out.phymstr_ack && ck-1-ack_at<=256) else $fatal(1,"recovered channel handback");
    if(mode<2) begin
      for(int r=0;r<int'(cfg.profile.ranks);r++) assert(physical_at[r]>ack_at) else $fatal(1,"missing active rank REF");
    end else assert(physical_at[0]<0) else $fatal(1,"unexpected REF in short thermal-publication window");
    phy_refresh_done=0;pin_owner=0;pin_cs=0;pin_ca=0;
    repeat(4) tick();assert(status_value.state==1) else $fatal(1,"quiet handback");
    send_command(CH_CMD_ENABLE);tick();assert(status_value.state==2 && status_value.memory_trusted) else $fatal(1,"fresh service");
    // No AXI claim: request source is empty. Let real deferred register/ZQ
    // owners finish after handback and preserve the continuous physical model.
    repeat(200) tick();
    assert(zq_count>=int'(cfg.profile.ranks)) else $fatal(1,"pending natural ZQ");
    $fclose(stream);$display("MASTER_REFRESH_CHANNEL mode=%0d ack=%0d REF=%0d/%0d MR4=%0d ZQ=%0d",mode,ack_at,physical_at[0],physical_at[1],mr4_count,zq_count);$finish;
  end
endmodule
