// Four historical and two selected real-wrapper cases. No internal state is assigned. The
// observed owner and actual output buses select the dirty injection window.
`include "wrapper_tail.svh"
int wm_case=-1,wm_boot=0;
bit wm_failed=0;
bit [1:0] wm_probe_accepted=0;
bit [1:0] wm_probe_pending=0;
logic [31:0] wm_stale_baseline=0;
int wm_selected_case=-1;
bit wm_churn_done=0,wm_rfm_requested=0;
lpddr5_mc_ca_t [CHANNELS-1:0] wm_phy_ca='0;

// AXI addresses preserve the canonical channel-bit6 mapping. Successive
// rows alternate the two banks sharing paired RAA0. The request is selected
// from the actual maintenance output, never from a predicted ACT count.
task automatic wm_rfm_churn;
  int at;
  bit failed_response;
  for(int n=0;n<512 && !wm_rfm_requested;n++) begin
    at=((n+1)<<16) | ((n%2)<<15);
    send_ar(0,16'(160+n%80),at,4);
    source_negative(0);rready[0]=1;
    for(int beat=0;beat<4;beat++) begin
      do source_positive(0);while(!rvalid[0]);
      failed_response=wm_rfm_requested && host_fault;
      check(r[0].id==16'(160+n%80) && r[0].last==(beat==3),"RFM AXI read identity/last");
      check(r[0].response==(failed_response ? 2 : 0) && r[0].data==0,"RFM AXI zero-image/error response");
      source_negative(0);
    end
    rready[0]=0;
  end
  wm_churn_done=1;
endtask

task automatic wm_active_refresh;
  int physical_tick;
  // This selected shape has one active rank. The external PHY drives its
  // physical REF directly, without the controller's two-CK command delay.
  // A subsequent sampled done reports that observed execution only.
  for(int n=0;n<256;n++) begin
    @(negedge clk);#0.001;
    check(!host_fault && management_out[0].phymstr_ack,"active master ownership lost");
    if(management_out[0].refresh_request==1) break;
  end
  check(management_out[0].refresh_request==1,"active master refresh request absent");
  wm_phy_ca[0]='{cs:2'b01,address:14'h2038};
  @(negedge clk);#0.001;physical_tick=wm_tick[0]-1;wm_phy_ca[0]='0;phy_refresh_done[0]=1;
  @(negedge clk);#0.001;phy_refresh_done[0]=0;
  // Full physical tRFC is deliberately conservative; ACT-2 endpoint
  // qualification remains separate. No maintenance-done input is forced.
  while(wm_tick[0]-1<physical_tick+int'(profile.dram_ck_trfc_ab)+2) begin
    @(negedge clk);#0.001;check(!host_fault,"active REF recovery fault");
  end
  check(management_out[0].refresh_request==0,"active refresh not consumed");
  report_valid[0]=0;repeat(2) @(negedge clk);
  report_sr[0]=0;report_valid[0]=1;repeat(2) @(negedge clk);
  wm_mark("active_refresh_recovered");
endtask

// Hold both legal address requests after the crossing closes admission. They
// remain asserted through handback/recovery and complete normally on return.
task automatic wm_probe(input int p,id);
  repeat(4) source_positive(p);source_negative(p);
  // Explicit sequential blocks keep each timed driver together when the
  // qualified simulator inlines tasks inside a fork.
  fork
    begin send_aw(p,16'(id),512+128*p,4);end
    begin send_ar(p,16'(id+1),576+128*p,4);end
  join
  wm_probe_accepted[p]=1;
endtask
task automatic wm_finish_probes(input int id);
  for(int n=0;n<3000;n++) begin
    @(negedge clk);if(&wm_probe_accepted) break;
  end
  check(&wm_probe_accepted,"both address probes accepted after return");
  wm_mark("probes_accepted");
  for(int p=0;p<2;p++) begin
    inline_payload(p,512+128*p,8'(id+2*p));
    read_result(p,16'(id+2*p+1),576+128*p,4);
  end
  wait_settled();
endtask
task automatic wm_mark(input string name);
  $fdisplay(wm_stream,"{\"kind\":\"marker\",\"name\":\"%s\",\"ps\":%0d,\"case\":%0d,\"failed\":%0d,\"boot\":%0d,\"count\":%0d}",
    name,observer.timestamp_ps(),wm_case,wm_failed,wm_boot,dut.stale_completions);
endtask
task automatic wm_wait_ready(input bit response_b);
  for(int n=0;n<3000;n++) begin
    source_negative(1);if(response_b ? bvalid[1] : rvalid[1]) return;
  end
  $fatal(1,"wrapper held response did not arrive");
endtask
task automatic wm_held_read(input int id,input bit failed);
  source_negative(1);rready[1]=1;
  for(int n=0;n<4;n++) begin
    do source_positive(1);while(!rvalid[1]);
    check(r[1].id==16'(id) && r[1].last==(n==3),"held R identity/last");
    check(r[1].response==(failed && n!=0 ? 2 : 0),"exposed R preserved, unoffered R fails");
    for(int j=0;j<BYTES;j++)
      check(r[1].data[j*8+:8]==(failed && n!=0 ? 8'd0 : expected[64+n*BYTES+j]),"held R byte snapshot");
    source_negative(1);
  end
  rready[1]=0;
endtask
task automatic wm_fresh_service(input int id);
  int prior;
  prior=b_count[0];send_aw(0,16'(id),0,4);inline_payload(0,0,8'(id));wait_b(0,16'(id),prior+1);
  send_ar(0,16'(id+1),0,4);read_result(0,16'(id+1),0,4);wait_settled();
  check(!host_fault && dut.stale_completions==wm_stale_baseline,"fresh protected service");wm_mark("fresh_service");
endtask
task automatic wm_case_run(input int kind,input bit failed);
  int base_id,prior0,prior1,dirty_words;
  bit seen_check;
  check(kind inside {0,1,2,3},"wrapper management kind");
  wm_case=kind==2 ? 4 : kind==3 ? 5 : 2*kind+int'(failed);
  wm_failed=failed;base_id=20+wm_case*20;
  wm_mark("begin");
  // Wait without traffic for a complete natural REF/MR4/ZQ renewal on
  // BOTH channels. Raw command origins are earlier than timer renewal, so
  // this selection cannot gain headroom from a delayed internal done pulse.
  for(int n=0;n<8000;n++) begin
    @(negedge clk);#0.001;
    if(wm_ready_window(WM_WINDOW_TOTAL_CK,1)) break;
  end
  check(enable && wm_ready_window(WM_WINDOW_TOTAL_CK,1),"natural both-channel maintenance window unavailable");
  wm_setup_ps=observer.timestamp_ps();wm_setup_live=1;wm_mark("maintenance_ready");
  prior1=b_count[1];send_aw(1,16'(base_id),64,4);inline_payload(1,64,8'(base_id));wait_b(1,16'(base_id),prior1+1);wait_settled();
  drive_bready(0);prior0=b_count[0];prior1=b_count[1];
  send_aw(1,16'(base_id+1),64,4);inline_payload(1,64,8'(base_id+1));wm_wait_ready(1);
  send_ar(1,16'(base_id+2),64,4);wm_wait_ready(0);
  check(b[1].response==0 && r[1].response==0,"actually exposed success B/R");
  send_aw(0,16'(base_id+3),0,4);inline_payload(0,0,8'(base_id+3));
  if(failed) begin
    send_aw(0,16'(base_id+4),256,4);send_w(0,256,4,0,1);
    if(kind!=2) send_ar(0,16'(base_id+5),256,4);
  end
  // Count actual second-segment write enables while data has committed but
  // the check operation is still live. Twelve words place the request in
  // the final check transfer; the independent replay verifies this window.
  dirty_words=0;seen_check=0;
  for(int n=0;n<4000;n++) begin
    @(negedge clk);#0.001;
    if(dut.datapath.channels[0].channel.inline_ecc.data_committed_q) begin
      seen_check=1;
      dirty_words+=$countones(writes[0].enable)/2;
      if(dirty_words>=12) break;
    end
  end
  check(seen_check && dirty_words==12 && !settled && !status_value[0].ecc_drained,"dirty final check transfer window");
  check(wm_ready_window(WM_WINDOW_REQUEST_CK,0),"dirty request maintenance reserve/phase");
  check(observer.timestamp_ps()-wm_setup_ps<=64'(WM_WINDOW_SETUP_CK)*1250,"all priming/setup exceeded budget");
  wm_setup_live=0;
  if(kind inside {2,3}) wm_mark("extension_setup");
  if(kind==2) begin
    // Retire the data/check write normally, keeping the exposed port1 B/R
    // and port0 trailing-W obligation live. Ordinary REF/MR4/ZQ stay enabled.
    wm_churn_done=0;wm_rfm_requested=0;
    fork begin wm_rfm_churn();end join_none
    for(int n=0;n<120000;n++) begin
      @(negedge clk);#0.001;
      if(dut.datapath.channels[0].channel.maintenance_valid && dut.datapath.channels[0].channel.maintenance_rfm) break;
      check(!wm_churn_done && !host_fault,"natural AXI ACT-debt stimulus exhausted/faulted");
    end
    check(dut.datapath.channels[0].channel.maintenance_valid && dut.datapath.channels[0].channel.maintenance_rfm
      && dut.datapath.channels[0].channel.maintenance.raa_q[0][0]>=63,"mandatory RFM naturally pending");
    wm_rfm_requested=1;wm_mark("rfm_request");
  end else if(kind==3) begin
    // Release actual source responses before choosing the risky physical
    // retention window. This is an empty-host entry with real AXI history.
    wm_held_read(base_id+2,0);drive_bready(1);wait_settled();
    for(int n=0;n<8000;n++) begin
      @(negedge clk);#0.001;
      if(dut.datapath.channels[0].channel.refresh_budget inside {[300:400]}
          && status_value[0].drained && status_value[1].drained
          && !dut.datapath.channels[0].channel.maintenance_urgent) break;
    end
    check(dut.datapath.channels[0].channel.refresh_budget inside {[300:400]}
      && status_value[0].drained && status_value[1].drained,"natural risky active-master window absent");
    wm_mark("active_request");
  end else wm_mark("request");
  if(kind==0) begin management[0].phyupd_type=0;management[0].phyupd_req=1;end
  else begin management[0].phymstr_type=0;management[0].phymstr_cs_state=1;management[0].phymstr_state_sel=0;management[0].phymstr_req=1;end
  wm_probe_accepted=0;wm_probe_pending=0;
  fork
    begin wm_probe(0,base_id+12);end
    begin wm_probe(1,base_id+14);end
  join_none
  if(failed) begin
    if(kind==2) begin
      for(int n=0;n<int'(profile.management_ck_tphymstr_resp)+4;n++) begin
        @(negedge clk);#0.001;
        check(!management_out[0].phymstr_ack && !management[0].error,"RFM entry early ACK/external error");
        if(status_value[0].fault) break;
      end
      check(status_value[0].fault && status_value[0].fault_code==12,"natural first-overdue master timeout");
      wm_mark("error");
    end else begin repeat(2) @(negedge clk);management[0].error=1;wm_mark("error");end
    for(int n=0;n<100;n++) begin @(negedge clk);if(host_fault && !enable) break;end
    check(host_fault && !enable && !settled,"external error with surviving obligations");
    write_csr(INDEX_CONTROL,8);wm_mark("recover_requested");
    repeat(8) begin @(negedge clk);check(!reset_request,"destructive reset before source settlement");end
    // W consumes the rest of the original accepted descriptor under fatal.
    send_w(0,256,4,1);
    wm_held_read(base_id+2,1);
    if(kind==2) begin
      for(int n=0;n<1000;n++) begin @(negedge clk);if(wm_churn_done) break;end
      check(wm_churn_done,"fatal RFM read AXI settlement");
    end else read_result(0,16'(base_id+5),256,4,2);
    drive_bready(1);
    for(int n=0;n<1000;n++) begin @(negedge clk);if(b_count[0]==prior0+2 && b_count[1]==prior1+1 && settled) break;end
    check(b_count[0]==prior0+2 && b_count[1]==prior1+1 && settled,"all W/R/B settled after fatal");
    check(&wm_probe_pending,"both source probes pending before recovery");
    wm_mark("source_settled");
    for(int n=0;n<100;n++) begin @(negedge clk);if(reset_request) break;end
    check(reset_request,"settlement permits reset request");
    repeat(8) begin @(negedge clk);check(reset_request && status_value[0].boot_epoch==32'(wm_boot),"stale high confirmation");end
    wm_mark("stale_confirmation_rejected");
    // Remove failed external ownership claims only as part of the confirmed
    // destructive reset. This explicitly cancels live memory work.
    @(negedge clk);management[0].error=0;management[0].phyupd_req=0;management[0].phymstr_req=0;
    reset_confirmed=0;
    repeat(4) @(negedge clk);reset_confirmed='1;
    wm_boot++;
    for(int n=0;n<300;n++) begin
      @(negedge clk);
      if(!host_fault && status_value[0].boot_epoch==32'(wm_boot) && status_value[1].boot_epoch==32'(wm_boot)
          && !dut.datapath.channels[0].channel.destructive_reset && !dut.datapath.channels[1].channel.destructive_reset) break;
    end
    check(!host_fault && settled && !enable && !dut.host_config_active && !reset_request && !memory_reset && (&reset_confirmed)
      && status_value[0].boot_epoch==32'(wm_boot) && status_value[1].boot_epoch==32'(wm_boot)
      && !status_value[0].config_valid && !status_value[1].config_valid
      && !status_value[0].train_done && !status_value[1].train_done
      && !dut.datapath.channels[0].channel.destructive_reset && !dut.datapath.channels[1].channel.destructive_reset,
      "both channels require fresh epoch/config/training");
    // Preserve the cumulative production diagnostic. Independent host seam
    // replay must first reconcile every earlier event and numeric update,
    // prove both destructive recoveries, and reject every later unmatched
    // event even if the counter is saturated. Publication/probes follow this.
    wm_stale_baseline=dut.stale_completions;wm_mark("recovery_baseline");
    for(int i=0;i<65536;i++) expected[i]=0;
    wait_status(INDEX_BOOT_EPOCH,32'hffffffff,32'(wm_boot));
    repeat(30) @(negedge pclk);publish_and_boot(wm_boot);wm_mark("retrained");
  end else begin
    if(kind!=3) begin
      repeat(8) begin @(negedge clk);check(!management_out[0].phyupd_ack && !management_out[0].phymstr_ack && !settled,"held source prevents ownership");end
      wm_mark("release_responses");wm_held_read(base_id+2,0);drive_bready(1);
    end
    for(int n=0;n<96;n++) begin
      @(negedge clk);check(!host_fault,"bounded unsolicited drain fault");
      if(kind==0 ? management_out[0].phyupd_ack : management_out[0].phymstr_ack) break;
    end
    check(kind==0 ? management_out[0].phyupd_ack : management_out[0].phymstr_ack,"actual unsolicited ACK");
    check(settled && b_count[0]==prior0+1 && b_count[1]==prior1+1,"ACK after actual source settlement");
    check(&wm_probe_pending,"both source probes pending before handback");
    wm_mark("ack");
    if(kind==0) begin repeat(4) @(negedge clk);management[0].phyupd_req=0;end
    else if(kind==3) begin wm_active_refresh();management[0].phymstr_req=0;end
    else begin
      report_valid[0]=0;repeat(3) @(negedge clk);
      report_sr[0]=0;report_valid[0]=1;repeat(3) @(negedge clk);
      check(management_out[0].refresh_request==0,"no refresh due in short master window");
      management[0].phymstr_req=0;
    end
    for(int n=0;n<100;n++) begin @(negedge clk);if(status_value[0].state==1 && !management_out[0].phyupd_ack && !management_out[0].phymstr_ack) break;end
    check(status_value[0].state==1 && status_value[0].memory_trusted,"legal management handback");
    write_csr(INDEX_CONTROL,1);wait(enable);wm_mark("returned");
  end
  // Existing AR probes may already be accepted on this source port. Retire
  // their exact R streams before starting preservation reads on the same bus.
  wm_finish_probes(base_id+12);
  if(kind==3) begin
    send_ar(1,16'(base_id+8),64,4);read_result(1,16'(base_id+8),64,4);
    send_ar(0,16'(base_id+9),0,4);read_result(0,16'(base_id+9),0,4);
    wm_mark("preserved_service");
  end
  wm_fresh_service(base_id+10);
  // No new source work follows fresh_service. Preserve natural maintenance
  // and all five quiescent owners. The generated bound accounts for staggered
  // page/WCK recovery and carry-in/arriving REF, RFM, MR4 and ZQ on both channels.
  check(mr4_value[0]==9 && mr4_value[1]==9,"final tail thermal fixture binding");
  check(dut.datapath.channels[0].channel.active.page_idle_ck==WM_FINAL_PAGE_IDLE_CK
    && dut.datapath.channels[1].channel.active.page_idle_ck==WM_FINAL_PAGE_IDLE_CK,"final tail page policy binding");
  for(int n=0;n<WM_FINAL_TAIL_CK;n++) begin
    @(negedge clk);
    check(settled && !host_fault && dut.stale_completions==wm_stale_baseline,"final tail source settlement/trust/counter");
    if(status_value[0].drained && status_value[1].drained) break;
  end
  if(!(status_value[0].drained && status_value[1].drained)) begin
    wm_observers[0].tail_diagnostic();wm_observers[1].tail_diagnostic();
    $fatal(1,"fresh service all-owner tail exceeded %0dCK",WM_FINAL_TAIL_CK);
  end
  check(status_value[0].drained && status_value[1].drained,"fresh service physical/WCK tail");
  wm_mark("end_case");
endtask
task automatic wrapper_management_run;
  check(PORTS==2 && CHANNELS==2 && RANKS==1 && DATA_BITS==128,"wrapper management shape");
  if($value$plusargs("wrapper_case=%d",wm_selected_case)) begin
    check(wm_selected_case inside {4,5},"source-prepared wrapper case selection");
    wm_case_run(wm_selected_case==4 ? 2 : 3,wm_selected_case==4);
  end else for(int kind=0;kind<2;kind++) for(int failed=0;failed<2;failed++) wm_case_run(kind,1'(failed));
  wrapper_stop_observations();
  $display("CONTROLLER_WRAPPER_MANAGEMENT_PASS cases=%0d selected=%0d boot=%0d",wm_selected_case==-1 ? 4 : 1,wm_selected_case,wm_boot);
  $display("CONTROLLER_INTEGRATION_PASS checks=%0d wrapper_management=1",checks);$finish;
endtask
