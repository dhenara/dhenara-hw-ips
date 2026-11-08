module lpddr5_mc_channel_tb #(parameter bit PER_BANK_REFRESH=0);
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
  lpddr5_mc_channel_completion_t completion,held_completion;
  lpddr5_mc_channel_command_t command=CH_CMD_NONE;
  lpddr5_mc_channel_config_t cfg;
  lpddr5_mc_profile_t memory_profile,fsp_snapshot;
  lpddr5_mc_config_mailbox_t config_mailbox;
  lpddr5_mc_channel_status_t status_value;
  lpddr5_mc_management_in_t management_in;
  lpddr5_mc_management_out_t management_out;
  lpddr5_mc_ca_t ca;
  lpddr5_mc_wck4_t wck;
  lpddr5_mc_write_bus4_t write_value;
  lpddr5_mc_read_request4_t read_value;
  lpddr5_mc_return4_t return_value;
  int mr43_count,mr4_count,zq_count,refresh_count,rfm_count,cycles;
  logic notification_valid,notification_error_valid,first_error_valid;
  logic notification_ready=1,memory_reset=0,check_fsp=0,mrw_pending=0;
  int mrw_count=0,mrw_address=0,last_mrw=0,sre_count=0,srx_count=0,last_srx=0;
  logic [127:0] mrw_seen=0;
  logic [1:0] expected_old_fsp=0,expected_target_fsp=1;
  logic [12:0] notification_events;
  logic [31:0] notification_boot,notification_epoch,first_error_boot,first_error_epoch;
  logic [63:0] first_error_version;
  lpddr5_mc_error_t notification_error;
  logic unused_debug;
  assign unused_debug=^{fsp_snapshot,notification_valid,notification_error_valid,notification_events,notification_boot,notification_epoch,notification_error,first_error_valid,first_error_version,first_error_boot,first_error_epoch,status_value,command_rejected,fsp_ready,fsp_declined,wck,prepared_epoch,committed_epoch,management_out};
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
  // Keep the CA-driven fixture in one process. Inlining it into every timed
  // task call creates an oversized simulator coroutine without adding checks.
  logic memory_sample=0,step_check=0;
  initial forever begin
    @(posedge step_check);
    if(dut.backend.epoch_fault) $display("epoch diagnostic ck=%0d first=%0d/%0d count=%0d/%0d closing=%b polling=%b settled=%b pollvalid=%0d service=%0d regowner=%0d pages=%0d wck=%0d data=%0d maint=%0d budget=%0d",
      dut.backend.epochs.ck_q,dut.backend.epochs.first_q[0],dut.backend.epochs.first_q[1],dut.backend.epochs.count_q[0],dut.backend.epochs.count_q[1],
      dut.backend.epochs.closing_q,dut.backend.epochs.polling_q,dut.backend.epochs.settled_q,dut.backend.epoch_poll,dut.service,dut.backend.register_owner_q,dut.pages_closed,dut.wck_disabled,dut.backend.data_drained,dut.maintenance_urgent,dut.refresh_budget);
    if(dut.backend.epoch_fault) for(int b=0;b<32;b++)
      if(dut.backend.scheduler.open_q[b] || dut.backend.scheduler.act_ready_q[b]>dut.backend.scheduler.ck_q)
        $display("page bank=%0d open=%0d actready=%0d preready=%0d schedck=%0d",b,dut.backend.scheduler.open_q[b],dut.backend.scheduler.act_ready_q[b],dut.backend.scheduler.pre_ready_q[b],dut.backend.scheduler.ck_q);
    if(dut.backend.epoch_fault) for(int r=0;r<2;r++) for(int j=0;j<16;j++)
      if(dut.backend.epochs.members_q[r][j].valid) $display("member rank=%0d slot=%0d tag=%0d physical=%0d",r,j,dut.backend.epochs.members_q[r][j].identity.tag,dut.backend.epochs.members_q[r][j].physical);
    if(status_value.fault) $display("control thermal=%0d MR4=%0d REF=%0d RFM=%0d budget=%0d urgent=%0d regstate=%0d reservereg=%0d",dut.thermal,mr4_count,refresh_count,rfm_count,dut.refresh_budget,dut.maintenance_urgent,dut.register_state_q,dut.register_reserve);
    if(status_value.fault) $display("maintenance ck=%0d lastref=%0d/%0d pair=%0d/%0d pages=%0d rows=%0d pin=%0d wck=%0d",
      dut.maintenance.ck_q,dut.maintenance.last_refresh_q[0],dut.maintenance.last_refresh_q[1],dut.maintenance.next_pair_q[0],dut.maintenance.next_pair_q[1],dut.pages_closed,dut.rows_closed,dut.pin_drained,dut.wck_disabled);
    assert(!status_value.fault) else $fatal(1,"connected channel fault CK%0d life=%0d code=%0d maint=%h reg=%0d data=%0d sched=%0d epoch=%0d wck=%0d",
      cycles,status_value.state,status_value.fault_code,dut.maintenance_diagnostic,dut.register_fault_q,dut.backend.data_fault,dut.backend.scheduler_fault,dut.backend.epoch_fault,dut.backend.wck_fault);
  end

  initial forever begin
    @(posedge memory_sample);
    if(rst_n && $test$plusargs("trace_epochs")) begin
      if(dut.backend.launch_valid && cfg.profile.write_link) $display("epoch add ck=%0d r=%0d tag=%0d ready=%0d dataready=%0d",cycles,dut.backend.launch.rank_index,dut.backend.launch_identity.tag,dut.backend.epoch_ready,dut.backend.data_ready);
      if(dut.backend.data_valid && cfg.profile.write_link) $display("epoch physical ck=%0d tag=%0d done=%0d",cycles,dut.backend.data_result.identity.tag,dut.backend.data_result.physical_done);
      if(ca.cs!=0 && cfg.profile.write_link) $display("pin ca ck=%0d cs=%b ca=%h epochdrain=%0d reserve=%0d open=%h selectedrank=%0d",cycles,ca.cs,ca.address,dut.backend.epoch_drain,dut.backend.epoch_reserve,dut.backend.scheduler.open_q,dut.backend.close_rank_q);
      if(dut.backend.register_accept) $display("register accept ck=%0d epoch=%0d rank=%0d",cycles,dut.backend.epoch_poll,dut.backend.epoch_poll ? dut.backend.epoch_poll_rank : dut.register_rank_q);
      if(dut.backend.reg_valid) $display("register result ck=%0d epoch=%0d rank=%0d",cycles,dut.backend.is_epoch_q,dut.backend.reg_rank);
    end

    if(rst_n && !memory_reset) begin memory_model.prepare();memory_model.sample();check_control_pins();end
    else memory_model.reset();
  end
  task automatic check_control_pins;
    logic [7:0] expected_value;
    if(ca.cs!=0) begin
      if(ca.address[6:0]==104) sre_count++;
      if(ca.address[6:0]==40) begin srx_count++;last_srx=cycles;end
      if(check_fsp) begin
        assert(ca.cs==3 && !request_ready && write_value.enable==0 && read_value.enable==0)
          else $fatal(1,"FSP exclusive command ownership");
        if(mrw_pending) begin
          assert(ca.address[5:0]==8 && cycles==last_mrw+1) else $fatal(1,"connected MRW pair");
          expected_value=fsp_snapshot.mr[mrw_address];
          if(mrw_count==0) expected_value={fsp_snapshot.mr[16][7:4],expected_old_fsp,expected_target_fsp};
          if(mrw_count==18) expected_value={fsp_snapshot.mr[16][7:4],expected_target_fsp,expected_target_fsp};
          assert({ca.address[6],ca.address[13:7]}==expected_value) else $fatal(1,"connected frozen MR value");
          mrw_count++;mrw_pending=0;
        end else begin
          assert(ca.address[6:0]==88 && cycles-last_mrw>=12) else $fatal(1,"connected MRW spacing/encoding");
          mrw_address=int'(ca.address[13:7]);mrw_pending=1;
          if(mrw_count inside {0,18}) assert(mrw_address==16) else $fatal(1,"connected FSP control order");
          else begin
            assert(mrw_address inside {1,2,3,10,11,12,14,15,17,18,19,20,22,24,30,41,58} && !mrw_seen[mrw_address])
              else $fatal(1,"connected missing/duplicate MR");
            mrw_seen[mrw_address]=1;
          end
        end
        last_mrw=cycles;
      end
    end
  endtask
  task automatic tick;
    #1;
    memory_sample=1;
    #1;clk=1;#1;clk=0;memory_sample=0;#1;cycles++;
  endtask
  task automatic step;
    tick();step_check=1;#1;step_check=0;

  endtask
  task automatic send_command(input lpddr5_mc_channel_command_t value);
    assert(command_ready) else $fatal(1,"command not ready");command=value;command_valid=1;tick();command_valid=0;
  endtask
  task automatic wait_quiet;
    for(int c=0;c<2000;c++) begin step();if(status_value.state==1 && status_value.drained) return;end
    $fatal(1,"quiescence timeout backend=%0d pins=%0d integrity=%0d pages=%0d wck=%0d",dut.backend_drained,dut.pin_drained,dut.integrity_drained,dut.pages_closed,dut.wck_disabled);
  endtask
  task automatic publish(input logic initial_publication);
    config_mailbox='0;config_mailbox.destinations=1;config_mailbox.epoch=cfg.config_epoch;config_mailbox.payload_crc=config_crc(cfg);config_mailbox.prepare=1;
    tick();config_mailbox.prepare=0;
    assert(prepared && !prepare_error && !status_value.fault) else $fatal(1,"configuration prepare %0d",initial_publication);
    config_mailbox.commit=1;tick();config_mailbox='0;
    assert(status_value.config_valid && status_value.config_epoch==cfg.config_epoch && !status_value.fault) else $fatal(1,"configuration publication");
  endtask
  task automatic boot;
    management_in.init_complete=0;step();management_in.init_complete=1;management_in.training_epoch=request_boot;
    repeat(3) step();assert(status_value.train_request && !status_value.train_done) else $fatal(1,"fresh training request");
    train_token=request_boot;train_ack=1;step();train_ack=0;wait_quiet();
    assert(status_value.memory_trusted && status_value.train_done) else $fatal(1,"boot trust");
    send_command(CH_CMD_ENABLE);step();assert(status_value.state==2) else $fatal(1,"enable");
  endtask
  function automatic lpddr5_mc_granule_t granule(input int n,input logic write_access);
    lpddr5_mc_granule_t g;
    g='0;g.tag=64'(n)+(write_access ? 1000 : 2000);g.generation=1;g.axi_id=16'(n);g.rank_index=1'(n%2);
    g.bank_group=2'(n/2);g.bank=2'(n/8);g.row=18'(n/32);g.column=12'((n%16)*2);g.qos_class=2'(n);
    g.address=(40'(g.rank_index)<<30)|(40'(g.row)<<15)|(40'({g.bank_group,g.bank})<<11)|(40'(g.column)<<5);
    g.is_write=write_access;g.is_64_bytes=1;g.byte_enable=n%3==0 ? 64'hffffffffffffffff : 64'hff00ff00ffff00ff;
    for(int b=0;b<64;b++) g.data[b*8+:8]=8'(n*17+b);
    return g;
  endfunction
  task automatic stream(input logic writes,input int count);
    int sent,received,n,deadline,idle_ck,start_ck;
    logic [95:0] seen;
    logic holding,progress;
    lpddr5_mc_granule_t expected;
    logic unused_expected;
    sent=0;received=0;seen=0;holding=0;idle_ck=0;start_ck=cycles;
    // Strict link epochs serialize MR43 and read-clear/WCK recovery for every
    // physical half when the admitted drain reserve nearly fills the poll
    // bound. REFpb adds complete rank/pair rounds. Scale the total deadline
    // with that work, while independently rejecting a loss of host progress.
    deadline=cfg.profile.write_link ? count*1024+4096 : 24000;
    for(int c=0;c<deadline;c++) begin
      progress=0;
      request=granule(sent,writes);request_valid=sent<count;completion_ready=c%11<7;
      #1;
      if(holding) assert(completion_valid && completion==held_completion) else $fatal(1,"stalled connected completion");
      holding=completion_valid && !completion_ready;held_completion=completion;
      if(request_valid && request_ready) begin sent++;progress=1;end
      if(completion_valid && completion_ready) begin
        n=int'(completion.identity.tag)-(writes ? 1000 : 2000);expected=granule(n,1);unused_expected=^expected;
        assert(n>=0 && n<count && !seen[n] && completion.identity.boot_epoch==request_boot && completion.identity.config_epoch==request_epoch
          && completion.identity.generation==1 && completion.physical_done && completion.integrity_done && !completion.failed && completion.is_write==writes)
          else $fatal(1,"connected completion identity/stage n=%0d",n);
        if(!writes) for(int b=0;b<64;b++)
          assert(completion.data[b*8+:8]==(expected.byte_enable[b] ? expected.data[b*8+:8] : 8'd0)) else $fatal(1,"memory byte n=%0d b=%0d got=%h",n,b,completion.data[b*8+:8]);
        seen[n]=1;received++;progress=1;
      end
      idle_ck=progress ? 0 : idle_ck+1;
      assert(idle_ck<4096) else $fatal(1,"connected stream lost progress sent=%0d done=%0d idleCK=%0d",sent,received,idle_ck);
      step();if(received==count) break;
    end
    request_valid=0;completion_ready=1;step();
    if(sent!=count || received!=count) begin
      $display("stream stall writes=%0d link=%0d inline=%0d CK=%0d budget=%0d maint=%0d/%0d urgent=%0d reg=%0d reserve=%0d epoch=%0d/%0d WCK=%0d data=%0d pages=%0d",
        writes,cfg.profile.write_link,cfg.inline_ecc,cycles,dut.refresh_budget,dut.maintenance_valid,dut.maintenance_ready,
        dut.maintenance_urgent,dut.register_state_q,dut.backend.link_reserve_hold,dut.backend.epoch_drain,dut.backend.epoch_poll,
        dut.wck_disabled,dut.backend.data_drained,dut.pages_closed);
      $display("maintenance CK=%0d last=%0d/%0d pairs=%0d/%0d pending=%0d scheduler issued=%0d accepted=%0d MR43=%0d REF=%0d",
        dut.maintenance.ck_q,dut.maintenance.last_refresh_q[0],dut.maintenance.last_refresh_q[1],
        dut.maintenance.next_pair_q[0],dut.maintenance.next_pair_q[1],dut.maintenance.pending_q,dut.issued,dut.accepted_q,mr43_count,refresh_count);
    end
    assert(sent==count && received==count) else $fatal(1,"connected stream progress sent=%0d done=%0d",sent,received);
    $display("PASS stream progress writes=%0d count=%0d elapsedCK=%0d deadline=%0d",writes,count,cycles-start_ck,deadline);
  endtask
  task automatic run_case(input logic ratio_two,link_writes,inline_mode,input int divisor,input logic always_on=1);
    logic [63:0] stale_before;
    rst_n=0;request_valid=0;command_valid=0;config_mailbox='0;management_in='0;request='0;tick();rst_n=1;cycles=0;
    cfg='0;cfg.profile=fixture_profile(ratio_two,0,link_writes,always_on,0,1);cfg.profile.ranks=2;cfg.inline_ecc=inline_mode;
    cfg.service_div=3'(divisor);cfg.age_tick_div=8;cfg.read_urgent_age=4;cfg.write_urgent_age=4;cfg.bw_window=64;cfg.read_weight=1;cfg.write_weight=1;cfg.config_epoch=1;
    memory_profile=cfg.profile;
    request_boot=0;request_epoch=1;host_settled=1;reset_confirmed=0;report_valid=3;report_sr=0;phy_refresh_done=0;mr43=0;mr4=9;completion_ready=1;
    publish(1);boot();
    stream(1,inline_mode ? 24 : 80);stream(0,inline_mode ? 24 : 80);
    // Exercise real periodic MR4/ZQ and periodic refresh after the stream.
    for(int c=0;c<3600;c++) step();
    assert(mr4_count>=2 && zq_count>=2 && refresh_count>=2) else $fatal(1,"connected maintenance missing MR4=%0d ZQ=%0d REF=%0d",mr4_count,zq_count,refresh_count);
    if(link_writes) assert(mr43_count>2) else $fatal(1,"missing real MR43");
    // Epoch-fenced stale transport is consumed without side effects.
    request=granule(90,1);request_epoch=0;request_valid=1;stale_before=dut.stale_q;
    // REFpb may own a complete bounded maintenance round at this instant.
    // Retain VALID/payload through that backpressure, then count one consume.
    #1;
    for(int c=0;c<3000 && !request_ready;c++) step();
    assert(request_ready) else $fatal(1,"stale request drain deadline");
    step();request_valid=0;request_epoch=1;
    assert(dut.stale_q==stale_before+64'd1) else $fatal(1,"stale request consumed more or less than once");
    repeat(20) step();assert(!completion_valid) else $fatal(1,"stale request produced completion");
    host_settled=0;send_command(CH_CMD_QUIESCE);repeat(80) step();assert(status_value.state==3) else $fatal(1,"host settlement bypass");
    host_settled=1;wait_quiet();assert(status_value.drained) else $fatal(1,"true final drain");
    $display("PASS connected stream ratio=%0d divisor=%0d link=%0d inline=%0d always_on=%0d MR4=%0d ZQ=%0d REF=%0d MR43=%0d",cfg.profile.ratio,divisor,link_writes,inline_mode,always_on,mr4_count,zq_count,refresh_count,mr43_count);
  endtask
  task automatic invalid_ingress(input logic inline_mode);
    rst_n=0;request_valid=0;command_valid=0;config_mailbox='0;management_in='0;request='0;tick();rst_n=1;cycles=0;
    cfg='0;cfg.profile=fixture_profile(0,0,0,1,0,0);cfg.profile.ranks=1;cfg.inline_ecc=inline_mode;
    cfg.service_div=2;cfg.age_tick_div=8;cfg.bw_window=64;cfg.config_epoch=1;
    memory_profile=cfg.profile;
    request_boot=0;request_epoch=1;host_settled=1;report_valid=1;report_sr=0;completion_ready=0;
    publish(1);boot();
    for(int bad=0;bad<2;bad++) begin
      request='0;request.tag=64'd500+64'(bad);request.generation=4;request.is_write=1;request.byte_enable='1;
      request.rank_index=bad==0;request.channel_index=bad==1;request_valid=1;#1;
      assert(request_ready) else $fatal(1,"invalid ingress stalled");step();request_valid=0;
      for(int c=0;c<20;c++) begin step();if(completion_valid) break;end
      assert(completion_valid && completion.failed && completion.response==2 && !completion.physical_done && completion.integrity_done
        && completion.identity.tag==request.tag && completion.identity.generation==4 && completion.identity.config_epoch==1 && status_value.issued==0)
        else $fatal(1,"invalid rank/channel completion");
      held_completion=completion;repeat(5) begin step();assert(completion_valid && completion==held_completion && status_value.issued==0) else $fatal(1,"invalid ingress stability/side effect");end
      completion_ready=1;step();completion_ready=0;repeat(3) step();
    end
    completion_ready=1;assert(!completion_valid && status_value.memory_trusted) else $fatal(1,"invalid ingress damaged channel");
    $display("PASS connected malformed rank/channel inline=%0d: failed stable identity with no memory command",inline_mode);
  endtask
  task automatic row_churn;
    int sent,received,n;
    logic [159:0] seen;
    sent=0;received=0;seen=0;
    for(int c=0;c<24000;c++) begin
      request=granule(sent,1);request.tag=64'd3000+64'(sent);request.rank_index=0;
      request.bank_group=sent%2==0 ? 0 : 2;request.bank=0;request.row=18'(sent/2);request.column=0;request.byte_enable='1;
      request.address=(40'(request.row)<<15)|(40'({request.bank_group,request.bank})<<11);
      request_valid=sent<160;#1;if(request_valid && request_ready) sent++;
      if(completion_valid && completion_ready) begin
        n=int'(completion.identity.tag)-3000;
        assert(n>=0 && n<160 && !seen[n] && !completion.failed && completion.physical_done && completion.integrity_done)
          else $fatal(1,"row churn completion");
        seen[n]=1;received++;
      end
      step();if(received==160) break;
    end
    request_valid=0;step();assert(sent==160 && received==160) else $fatal(1,"row churn progress");
  endtask
  task automatic setup_control;
    rst_n=0;request_valid=0;command_valid=0;config_mailbox='0;management_in='0;request='0;
    check_fsp=0;notification_ready=1;memory_reset=0;tick();rst_n=1;cycles=0;
    cfg='0;cfg.profile=fixture_profile(0,0,0,1,0,1);cfg.profile.ranks=2;
    cfg.service_div=2;cfg.age_tick_div=8;cfg.read_urgent_age=4;cfg.write_urgent_age=4;cfg.bw_window=64;cfg.read_weight=1;cfg.write_weight=1;cfg.config_epoch=1;
    memory_profile=cfg.profile;request_boot=0;request_epoch=1;host_settled=1;reset_confirmed=0;
    report_valid=3;report_sr=0;phy_refresh_done=0;train_ack=0;completion_ready=1;mr43=0;mr4=9;
    target_fsp=1;target_frequency=0;sre_count=0;srx_count=0;last_srx=0;
    publish(1);boot();
  endtask
  task automatic wait_low_power;
    for(int c=0;c<2000;c++) begin
      if(management_out.lp_ctrl_req && management_out.lp_data_req) return;
      step();
    end
    $fatal(1,"connected LP admission");
  endtask
  task automatic enter_sr;
    send_command(CH_CMD_SELF_REFRESH);wait_low_power();
    management_in.lp_ctrl_ack=1;management_in.lp_data_ack=1;
    for(int c=0;c<8;c++) begin step();if(status_value.state==4) return;end
    $fatal(1,"connected SRE");
  endtask
  task automatic wake_sr;
    int request_edge,release_edge,prior_srx;
    send_command(CH_CMD_WAKE);management_in.lp_ctrl_ack=0;management_in.lp_data_ack=0;
    request_edge=-1;release_edge=-1;prior_srx=srx_count;
    for(int c=0;c<1000;c++) begin
      if(management_out.ctrlupd_req && request_edge<0) request_edge=cycles;
      if(request_edge>=0 && !management_out.ctrlupd_req && release_edge<0) release_edge=cycles;
      // Respond at sampled edge one, deassert at edge two.
      management_in.ctrlupd_ack=request_edge>=0 && cycles==request_edge;
      step();
      if(srx_count>prior_srx) begin
        assert(request_edge>=0 && release_edge>=request_edge+4 && last_srx>release_edge)
          else $fatal(1,"SRX preceded complete registered update request=%0d release=%0d SRX=%0d",request_edge,release_edge,last_srx);
        break;
      end
    end
    management_in.ctrlupd_ack=0;wait_quiet();
    assert(last_srx>release_edge && cycles-last_srx>=int'(memory_profile.dram_ck_txsr)) else $fatal(1,"connected tXSR");
  endtask
  task automatic control_case(input int kind);
    lpddr5_mc_channel_config_t target_config;
    lpddr5_mc_channel_notification_t retained_notification;
    int release_edge,old_refresh,old_sre,old_srx,request_edge;
    logic saw_decline;
    setup_control();
    if(kind==0) begin
      stream(1,8);send_command(CH_CMD_QUIESCE);wait_quiet();
      target_config=cfg;target_config.profile=fixture_profile(1,0,0,1,0,1);target_config.profile.ranks=2;target_config.config_epoch=2;
      cfg=target_config;config_mailbox='0;config_mailbox.destinations=1;config_mailbox.epoch=2;
      config_mailbox.payload_crc=config_crc(cfg);config_mailbox.prepare=1;step();config_mailbox.prepare=0;
      assert(prepared && !prepare_error && committed_epoch==1 && management_out.frequency==1) else $fatal(1,"staging changed live point");
      fsp_snapshot=cfg.profile;expected_old_fsp=0;expected_target_fsp=1;
      send_command(CH_CMD_FSP);
      for(int c=0;c<2000;c++) begin step();if(management_out.init_start) break;end
      assert(management_out.init_start && !fsp_ready) else $fatal(1,"connected FSP request");
      check_fsp=1;mrw_count=0;mrw_seen=0;mrw_pending=0;last_mrw=cycles-100;
      management_in.init_complete=0;release_edge=-1;
      // Only the prepared immutable bank may influence programming.
      cfg='0;target_fsp=2;target_frequency=1;
      for(int c=0;c<1000;c++) begin
        if(!management_out.init_start && release_edge<0) release_edge=cycles;
        if(release_edge>=0 && cycles>=release_edge+2) management_in.init_complete=1;
        assert(management_out.frequency==0 && management_out.fsp==1 && management_out.frequency_ratio==1)
          else $fatal(1,"unfrozen FSP metadata");
        step();if(fsp_ready) break;
      end
      check_fsp=0;
      assert(fsp_ready && mrw_count==19 && !mrw_pending && release_edge>last_mrw+207 && committed_epoch==1 && !request_ready)
        else $fatal(1,"connected FSP programming/guard/readiness");
      cfg=target_config;config_mailbox.commit=1;step();config_mailbox='0;memory_profile=cfg.profile;request_epoch=2;
      wait_quiet();assert(committed_epoch==2 && management_out.frequency==0 && management_out.fsp==1) else $fatal(1,"connected common FSP commit");
      send_command(CH_CMD_ENABLE);step();stream(0,8);
      // An unchanged high is a normal decline with no target MR programming.
      send_command(CH_CMD_QUIESCE);wait_quiet();target_config=cfg;
      cfg.profile=fixture_profile(0,0,0,1,0,1);cfg.profile.ranks=2;cfg.config_epoch=3;
      config_mailbox='0;config_mailbox.destinations=1;config_mailbox.epoch=3;config_mailbox.payload_crc=config_crc(cfg);config_mailbox.prepare=1;
      step();config_mailbox.prepare=0;target_fsp=2;target_frequency=1;send_command(CH_CMD_FSP);saw_decline=0;
      for(int c=0;c<2000;c++) begin
        step();if(fsp_declined) saw_decline=1;
        if(saw_decline && !prepared && status_value.state==1) break;
      end
      assert(saw_decline && !prepared && committed_epoch==2 && management_out.frequency==0 && management_out.fsp==1)
        else $fatal(1,"connected decline changed active point");
      cfg=target_config;send_command(CH_CMD_ENABLE);step();stream(1,8);stream(0,8);
      $display("PASS connected FSP: frozen 19 MR pairs, fresh low/high, guard, common epoch commit, retained data and normal decline");
    end else if(kind==1) begin
      stream(1,8);send_command(CH_CMD_QUIESCE);wait_quiet();
      send_command(CH_CMD_POWER_DOWN);wait_low_power();repeat(8) step();
      assert(status_value.state==1 && !management_out.lp_ctrl_req && sre_count==0) else $fatal(1,"connected LP decline");
      enter_sr();old_refresh=refresh_count;old_sre=sre_count;
      repeat(8000) begin step();assert(status_value.state==4 && ca.cs==0) else $fatal(1,"long connected SR");end
      assert(refresh_count==old_refresh) else $fatal(1,"external refresh during all-rank SR");
      wake_sr();old_refresh=refresh_count;old_srx=srx_count;
      enter_sr();
      assert(sre_count==old_sre+1 && refresh_count>=old_refresh+2 && srx_count==old_srx)
        else $fatal(1,"missing extra REF before connected SRE reentry");
      repeat(20) step();wake_sr();send_command(CH_CMD_ENABLE);step();stream(0,8);
      $display("PASS connected low power: normal decline, long SR, actual update before SRX, tXSR, extra refresh before reentry and retained data");
    end else if(kind==2) begin
      stream(1,8);send_command(CH_CMD_QUIESCE);wait_quiet();
      management_in.phyupd_req=1;management_in.phyupd_type=2;
      for(int c=0;c<96;c++) begin
        step();if(management_out.phyupd_ack) break;
        assert(ca.cs==0) else $fatal(1,"CA during PHY update drain");
      end
      assert(management_out.phyupd_ack && status_value.owner==LPDDR5_MC_OWN_PHY && status_value.state==3)
        else $fatal(1,"PHY acknowledged outside lifecycle ownership window");
      repeat(6) step();management_in.phyupd_req=0;wait_quiet();
      for(int use_sr=0;use_sr<2;use_sr++) begin
        management_in.phymstr_req=1;management_in.phymstr_type=0;management_in.phymstr_state_sel=1;
        management_in.phymstr_cs_state=use_sr==0 ? 3 : 0;
        for(int c=0;c<96;c++) begin step();if(management_out.phymstr_ack) break;end
        assert(management_out.phymstr_ack && management_out.syscs_state==3 && status_value.owner==LPDDR5_MC_OWN_PHY)
          else $fatal(1,"connected master entry");
        report_valid=0;step();report_sr=use_sr==0 ? 0 : 3;report_valid=3;
        repeat(20) begin step();assert(ca.cs==0 && !request_ready) else $fatal(1,"MC command under PHY master");end
        management_in.phymstr_req=0;wait_quiet();
      end
      send_command(CH_CMD_ENABLE);step();stream(0,8);
      $display("PASS connected PHY ownership: deferred PHY update acknowledgement, IDLE/SR master windows, fresh rank reports and retained data");
    end else if(kind==3) begin
      // A real late update acknowledgement loses ownership while host storage
      // is unsettled. The host fixture explicitly settles its obligations.
      notification_ready=0;host_settled=0;send_command(CH_CMD_UPDATE);
      repeat(10) step();assert(!management_out.ctrlupd_req && status_value.state==3) else $fatal(1,"unsettled update admission");
      host_settled=1;request_edge=-1;
      for(int c=0;c<2000;c++) begin step();if(management_out.ctrlupd_req) begin request_edge=cycles;break;end end
      assert(request_edge>=0) else $fatal(1,"recovery test update request");
      repeat(3) step();management_in.ctrlupd_ack=1;tick();management_in.ctrlupd_ack=0;repeat(3) tick();
      assert(status_value.fault && !status_value.memory_trusted && !request_ready && first_error_valid)
        else $fatal(1,"late update acknowledgement did not fault");
      retained_notification='0;retained_notification.events=notification_events;retained_notification.error_valid=notification_error_valid;
      retained_notification.error=notification_error;retained_notification.boot_epoch=notification_boot;retained_notification.config_epoch=notification_epoch;
      host_settled=0;reset_confirmed=1;send_command(CH_CMD_RECOVER);
      assert(command_rejected && status_value.fault && status_value.boot_epoch==0) else $fatal(1,"unsettled recovery accepted");
      host_settled=1;reset_confirmed=0;send_command(CH_CMD_RECOVER);
      assert(command_rejected && status_value.fault) else $fatal(1,"unconfirmed recovery accepted");
      reset_confirmed=1;send_command(CH_CMD_RECOVER);memory_reset=1;tick();memory_reset=0;reset_confirmed=0;
      assert(!status_value.fault && !status_value.memory_trusted && !status_value.train_done && !status_value.config_valid && status_value.boot_epoch==1)
        else $fatal(1,"connected destructive reset state");
      assert(notification_valid && notification_events==retained_notification.events && notification_error_valid==retained_notification.error_valid
        && notification_error==retained_notification.error && notification_boot==retained_notification.boot_epoch && notification_epoch==retained_notification.config_epoch
        && first_error_valid && first_error_boot==0) else $fatal(1,"recovery lost/relabelled diagnostics");
      cfg.boot_epoch=1;cfg.config_epoch=2;request_boot=1;request_epoch=2;
      publish(1);management_in.init_complete=1;management_in.training_epoch=1;repeat(8) step();
      assert(!status_value.train_request && !status_value.memory_trusted) else $fatal(1,"stale high initialization admitted recovery");
      management_in.init_complete=0;step();management_in.init_complete=1;repeat(3) step();train_ack=1;train_token=0;step();train_ack=0;
      assert(command_rejected && !status_value.train_done) else $fatal(1,"stale recovery token accepted");
      train_ack=1;train_token=1;step();train_ack=0;wait_quiet();notification_ready=1;
      send_command(CH_CMD_ENABLE);step();stream(1,8);stream(0,8);
      $display("PASS connected destructive recovery: real late-ack fault, settlement/reset gates, retained old-boot notifications, stale init/token rejection and fresh traffic");
    end else begin
      row_churn();$display("stress row churn finished CK%0d REF=%0d RFM=%0d",cycles,refresh_count,rfm_count);assert(rfm_count>0) else $fatal(1,"mandatory RFM not exercised");
      send_command(CH_CMD_QUIESCE);wait_quiet();mr4=13;
      repeat(5000) step();$display("stress hot13 finished CK%0d REF=%0d MR4=%0d",cycles,refresh_count,mr4_count);assert(dut.thermal==13) else $fatal(1,"hot MR4 not observed");
      send_command(CH_CMD_ENABLE);step();stream(1,8);stream(0,8);
      send_command(CH_CMD_QUIESCE);wait_quiet();mr4=15;
      repeat(5000) step();assert(dut.thermal==15) else $fatal(1,"hottest MR4 not observed");
      $display("PASS connected stress REFpb=%0d: paired RAA/RFM bound, hot thermal refresh/derating and MR4 service",PER_BANK_REFRESH);
    end
  endtask
  initial begin
    int selected_case;
    selected_case=0;
    if($value$plusargs("channel_case=%d",selected_case)) begin end
    cfg='0;request='0;config_mailbox='0;management_in='0;cycles=0;
    for(int c=0;c<10;c++) begin
      if(selected_case==0 || (selected_case==1 && c==6) || (selected_case==2 && c==7) || (selected_case==3 && c>=8))
        run_case(c<6 ? 1'(c%2) : c==9,c inside {6,7},c==7,c<6 ? (1<<(c/2)) : 2,c<8);
    end
    if(selected_case inside {0,4}) for(int c=0;c<2;c++) invalid_ingress(1'(c));
    for(int c=0;c<5;c++)
      if(selected_case==0 || (selected_case==5 && c<4) || (selected_case==6 && c==4)) control_case(c);
    $display("PASS selected channel cases selection=%0d",selected_case);$finish;
  end
endmodule
