// Three selectable actual-wrapper cases: unanimous decline, successful
// constant-CK ratio4->ratio2->ratio4, and accepted-low/stuck-return recovery.
int wf_case=-1;
bit wf_drive=0,wf_probe_done=0;
lpddr5_mc_profile_t [CHANNELS-1:0] wf_memory_profile;
string wf_target_sha;
for(genvar c=0;c<CHANNELS;c++) begin : wf_phy
  initial forever begin
    wait(wf_drive && management_out[c].init_start);
    // A full rising request/high sample precedes the fresh low. The driver
    // changes only the external PHY input on falling CK+1ps.
    if(wf_case!=0) begin
      @(posedge clk);
      @(negedge clk);#0.001;management[c].init_complete=0;
      wait(!management_out[c].init_start || !wf_drive);
      if(wf_case==1 && wf_drive) begin
        repeat(3+2*c) @(negedge clk);
        #0.001;management[c].init_complete=1;
      end
    end
    wait(!management_out[c].init_start || !wf_drive);
    wait(!wf_drive);
  end
end
task automatic wf_read_preserved(input int first_id);
  send_ar(0,16'(first_id),0,4);read_result(0,16'(first_id),0,4);
  send_ar(1,16'(first_id+1),64,4);read_result(1,16'(first_id+1),64,4);wait_settled();
endtask
task automatic wf_write_both(input int first_id);
  int prior;
  for(int p=0;p<2;p++) begin
    prior=b_count[p];send_aw(p,16'(first_id+p),64*p,4);
    inline_payload(p,64*p,8'(first_id+p));wait_b(p,16'(first_id+p),prior+1);
  end
  wait_settled();
endtask
task automatic wf_quiesce;
  write_csr(INDEX_CONTROL,2);wait_settled();
  for(int n=0;n<8000;n++) begin
    @(negedge clk);#0.001;
    check(!host_fault,"FSP actual QUIESCE fault");
    if(dut.quiesced && status_value[0].drained && status_value[1].drained) break;
  end
  check(dut.quiesced && status_value[0].drained && status_value[1].drained,"FSP independent tail prerequisite");
  for(int n=0;n<200;n++) begin
    @(negedge pclk);
    if(dut.global_pq.quiesced && dut.global_pq.settled) break;
  end
  check(dut.global_pq.quiesced && dut.global_pq.settled,"FSP crossed coherent APB QUIESCE/settlement");
endtask
task automatic wf_resume;
  write_csr(INDEX_CONTROL,1);
  for(int n=0;n<300;n++) begin @(negedge clk);if(enable) break;end
  check(enable && !host_fault,"FSP fresh host admission");
endtask
task automatic wf_publish(input int target);
  logic [31:0] old_epoch;
  old_epoch=dut.apb_epoch;wf_drive=1;
  wm_mark("fsp_publish_offer");write_csr(INDEX_DVFS,32'(target)|32'h100);
  if(wf_case==2) begin
    for(int n=0;n<8000;n++) begin @(negedge clk);#0.001;
      if(host_fault && !enable && dut.datapath.channels[0].channel.fsp_fault
        && dut.datapath.channels[1].channel.fsp_fault) break;end
    check(host_fault && !enable,"FSP stuck-return must fault and close source admission");
    wf_drive=0;wm_mark("fsp_failed");return;
  end
  for(int n=0;n<8000;n++) begin
    @(negedge clk);#0.001;check(!host_fault,"FSP normal publication fault");
    if(!dut.apb_busy && dut.apb_valid && (wf_case==0 || dut.apb_epoch>old_epoch)) break;
  end
  check(!dut.apb_busy && dut.apb_valid && dut.apb_epoch==(wf_case==0 ? old_epoch : dut.config_epoch),
    "FSP common final APB publication");
  if(wf_case==1) begin
    for(int n=0;n<500;n++) begin
      @(negedge clk);#0.001;
      if(dut.host_config_active && dut.config_epoch==dut.apb_epoch) break;
    end
    check(dut.host_config_active && status_value[0].config_epoch==dut.apb_epoch
      && status_value[1].config_epoch==dut.apb_epoch && dut.config_epoch==dut.apb_epoch,
      "FSP real host and both-channel installation");
  end else check(status_value[0].config_epoch==old_epoch && status_value[1].config_epoch==old_epoch
    && !dut.prepared[0] && !dut.prepared[1],"FSP decline retains epoch and clears both prepared images");
  wf_drive=0;wm_mark(wf_case==0 ? "fsp_declined" : "fsp_published");
endtask
task automatic wf_recover;
  write_csr(INDEX_CONTROL,8);wm_mark("fsp_recover_requested");wait_settled();
  for(int n=0;n<300;n++) begin @(negedge clk);if(reset_request) break;end
  check(reset_request,"FSP settled fatal must request destructive reset");
  repeat(8) begin @(negedge clk);check(reset_request && status_value[0].boot_epoch==0,"FSP stale-high reset confirmation");end
  wm_mark("fsp_stale_confirmation_rejected");
  @(negedge clk);reset_confirmed=0;
  repeat(4) @(negedge clk);reset_confirmed='1;wm_boot=1;
  for(int n=0;n<300;n++) begin
    @(negedge clk);#0.001;
    if(!host_fault && status_value[0].boot_epoch==1 && status_value[1].boot_epoch==1
      && !dut.datapath.channels[0].channel.destructive_reset && !dut.datapath.channels[1].channel.destructive_reset) break;
  end
  check(!host_fault && settled && !enable && !dut.host_config_active && !reset_request && !memory_reset
    && status_value[0].boot_epoch==1 && status_value[1].boot_epoch==1
    && !status_value[0].config_valid && !status_value[1].config_valid
    && !status_value[0].train_done && !status_value[1].train_done,"FSP both-channel reset invalidation");
  wm_stale_baseline=dut.stale_completions;wm_mark("fsp_recovery_baseline");
  for(int i=0;i<65536;i++) expected[i]=0;
  wait_status(INDEX_BOOT_EPOCH,32'hffffffff,1);
  repeat(30) @(negedge pclk);
  // Reset never rewrites staging. Select slot0 explicitly for fresh discovery.
  write_csr(INDEX_DVFS,0);publish_and_boot(1);wm_mark("fsp_retrained");
endtask
task automatic wrapper_fsp_run;
  int prior,dirty_words;
  bit seen_check;
  check(PORTS==2 && CHANNELS==2 && RANKS==1 && DATA_BITS==128 && PROFILE_NUMBER==0 && SERVICE_DIV==2,
    "FSP selected wrapper shape");
  check($value$plusargs("wrapper_fsp_target_sha256=%s",wf_target_sha),"missing FSP target binding");
  $fdisplay(wm_stream,"{\"kind\":\"fsp_binding\",\"ps\":%0d,\"case\":%0d,\"target_sha256\":\"%s\",\"initial_image\":\"0x%h\",\"target_image\":\"0x%h\",\"return_image\":\"0x%h\",\"register_count\":%0d,\"phase\":\"posedge_before_NBA\",\"apb_phase\":\"pclk_posedge_before_NBA\",\"initial_number\":0,\"target_number\":24,\"return_number\":0}",
    observer.timestamp_ps(),wf_case,wf_target_sha,qualified[0],qualified[1],qualified[2],REG_COUNT);
  wm_case=7+wf_case;wm_failed=wf_case==2;wm_mark("fsp_begin");
  wf_write_both(170);
  // The second-channel address is bit6. Keep nonzero images on both channels.
  prior=b_count[1];send_aw(1,172,64,4);inline_payload(1,64,8'd172);wait_b(1,172,prior+1);wait_settled();
  wf_read_preserved(173);wm_mark("fsp_initial_preserved");
  drive_bready(0);send_aw(1,176,64,4);inline_payload(1,64,8'd176);wm_wait_ready(1);
  send_ar(1,177,64,4);wm_wait_ready(0);
  send_aw(0,178,0,4);inline_payload(0,0,8'd178);
  dirty_words=0;seen_check=0;
  for(int n=0;n<4000;n++) begin
    @(negedge clk);#0.001;
    if(dut.datapath.channels[0].channel.inline_ecc.data_committed_q) begin
      seen_check=1;dirty_words+=$countones(writes[0].enable)/2;
      if(dirty_words>=12) break;
    end
  end
  check(seen_check && dirty_words==12 && !status_value[0].ecc_drained
    && !dut.global_pq.settled && !settled,"FSP dirty CONFIG_COMMIT rejection prerequisite");
  wm_mark("fsp_dirty");access(1,18'(REG_OFFSET[INDEX_CONFIG_COMMIT]),1,1);
  wm_mark("fsp_dirty_rejected");wm_held_read(177,0);drive_bready(1);wait_settled();
  wf_quiesce();wm_mark("fsp_quiesced");wf_probe_done=0;
  fork
    begin send_ar(0,190,0,4);wf_probe_done=1;end
  join_none
  wf_publish(1);
  if(wf_case==2) wf_recover();else wf_resume();
  for(int n=0;n<500;n++) begin @(negedge clk);if(wf_probe_done) break;end
  check(wf_probe_done,"FSP held actual AR was never admitted after return");
  read_result(0,190,0,4);wf_read_preserved(191);wm_mark("fsp_preserved");
  if(wf_case==1) begin
    // New writes and checks must use ratio2. Then switch back to slot2,
    // whose immutable descriptor is profile0, preserving the same memory.
    wf_write_both(193);wf_quiesce();wf_publish(2);wf_resume();
    send_ar(0,195,0,4);read_result(0,195,0,4);
    send_ar(1,196,64,4);read_result(1,196,64,4);wait_settled();wm_mark("fsp_roundtrip_preserved");
  end
  wf_quiesce();wm_mark("fsp_end");wrapper_stop_observations();
  $display("CONTROLLER_WRAPPER_FSP_PASS case=%0d boot=%0d",wf_case,wm_boot);
  $display("CONTROLLER_INTEGRATION_PASS checks=%0d wrapper_fsp=1",checks);$finish;
endtask
