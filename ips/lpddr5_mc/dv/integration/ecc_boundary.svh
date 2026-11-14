// Opt-in connected wrapper experiment. All writes use real AXI and the
// existing inline/link path; only public management/error inputs are driven.
// Timing observations below select an injection instant, never establish pins.
int eb_stream=0,eb_kind=0,eb_delta=0,eb_posted=0;
bit eb_armed=0,eb_scheduled=0,eb_fault=0,eb_trailing=0;
longint signed eb_anchor_fs=0;
string eb_path;
function automatic longint signed eb_now_fs();
  realtime captured;
  captured=$realtime;
  return longint'(captured*1000000.0);
endfunction
task automatic eb_select;
  check(INLINE_LIFECYCLE && !WRAPPER_MANAGEMENT && PROFILE_NUMBER==8,
    "ECC boundary requires inline/link profile8 controller without wrapper management");
  check($value$plusargs("ecc_boundary_kind=%d",eb_kind) && eb_kind inside {0,1},"ECC boundary kind");
  check($value$plusargs("ecc_boundary_delta=%d",eb_delta) && eb_delta inside {-1,0,1},"ECC boundary offset");
  check($value$plusargs("ecc_boundary_posted=%d",eb_posted) && eb_posted inside {0,1},"ECC boundary posted mode");
  check($value$plusargs("ecc_boundary_observations=%s",eb_path),"ECC boundary stream path");
  eb_stream=$fopen(eb_path,"w");check(eb_stream!=0,"ECC boundary stream open");
  $fdisplay(eb_stream,"{\"kind\":\"header\",\"version\":1,\"boundary\":%0d,\"delta_ck\":%0d,\"posted\":%0d,\"posted_early\":0,\"epoch_posted\":0,\"ck_fs\":1250000,\"pin_phase\":\"exclusive_last_beat_fs\",\"mr43_phase\":\"input_1fs_before_epoch_result_edge\"}",eb_kind,eb_delta,eb_posted);
endtask
task automatic eb_mark(input string name);
  $fdisplay(eb_stream,"{\"kind\":\"mark\",\"name\":\"%s\",\"fs\":%0d,\"anchor_fs\":%0d}",name,eb_now_fs(),eb_anchor_fs);
endtask
// Pin timing derives from the actual external WR32 CA and admitted paths.
// This is only a scheduling prediction: the offline pin owner must decode and
// validate every word and independently reproduce the exact fractional edge.
initial forever begin
  @(negedge clk);
  if(ECC_BOUNDARY && eb_armed && !eb_scheduled) begin
  // WR32 has C0 reserved low. CAS sync shares opcode[2:0]=4 but has
  // opcode[3]=1, so the complete low nibble must identify the write.
  if(eb_kind==0 && ca[0].cs==1 && ca[0].address[3:0]==4'd4) begin
    eb_anchor_fs=eb_now_fs()+((64'(profile.management_ck_tctrl_delay)+64'(profile.wl_ck))*8
      +64'(profile.phy_half_wck_dq_input)+48)*156250;
    eb_scheduled=1;eb_mark("pin_schedule");
  end
  // DISABLE's real input prerequisites predict RESULT's following consuming
  // edge. Observing these ports/state never substitutes for raw return/WCK
  // replay; the checker also requires the actual epoch result edge to match.
  if(eb_kind==1 && dut.datapath.channels[0].channel.backend.registers.state_q==4'd5
      && dut.datapath.channels[0].channel.backend.registers.result_address_o==43
      && dut.datapath.channels[0].channel.backend.registers.wck_disabled
      && dut.datapath.channels[0].channel.backend.registers.data_drained
      && dut.datapath.channels[0].channel.backend.registers.mrr_recovered) begin
    eb_anchor_fs=eb_now_fs()+1875000;eb_scheduled=1;eb_mark("mr43_schedule");
  end
  end
end
if(ECC_BOUNDARY) begin : eb_injection
initial begin : injector
  longint signed target_fs;
  wait(ECC_BOUNDARY && eb_scheduled);
  target_fs=eb_anchor_fs+64'(eb_delta)*1250000-(eb_kind==1 ? 1 : 0);
  check(target_fs>eb_now_fs(),"ECC boundary scheduling prediction in the past");
  #(real'(target_fs-eb_now_fs())/1000000.0);
  check(eb_trailing && bvalid[1] && b[1].id==108 && b[1].response==0 && !bready[1],
    "held offered B and accepted partial W must coexist at injection");
  check(!bvalid[0],"selected failed obligations must still be unoffered");
  if(eb_kind==0) management[0].error=1;
  else external_fault=1;
  eb_fault=1;eb_mark("inject");
end
end else begin : eb_no_injection
  // The injection-only assertion is absent in non-ECC fixture variants.
  logic unused_eb_trailing;
  assign unused_eb_trailing=^eb_trailing;
end
for(genvar ec=0;ec<CHANNELS;ec++) begin : eb_edges
  always @(posedge clk) if(ECC_BOUNDARY && eb_stream!=0) begin
    $fdisplay(eb_stream,"{\"kind\":\"edge\",\"fs\":%0d,\"channel\":%0d,\"armed\":%0d,\"epoch_result\":%0d,\"epoch_failed\":%0d,\"epoch_value\":%0d,\"fatal\":%0d,\"error\":%0d,\"external_fault\":%0d,\"late_error\":%0d,\"posted\":%0d,\"host_posted\":%0d,\"posted_early\":%0d,\"epoch_posted\":%0d}",
      eb_now_fs(),ec,eb_armed,dut.datapath.channels[ec].channel.backend.epoch_return,
      dut.datapath.channels[ec].channel.backend.epoch_return_failed,dut.datapath.channels[ec].channel.backend.reg_value,
      dut.datapath.host.fatal_i,management[ec].error,external_fault,dut.late_error,
      dut.datapath.channels[ec].channel.active.posted,dut.datapath.host.core.config_q.posted,
      dut.datapath.host.core.POSTED_EARLY,dut.datapath.channels[ec].channel.backend.epochs.posted_i);
  end
end
task automatic ecc_boundary_run;
  int seed_count,target_count;
  check(PORTS==2 && CHANNELS==2 && RANKS==1 && DATA_BITS==128,"ECC boundary shape");
  check(profile.write_link && !profile.read_link && !profile.write_dbi,"ECC boundary numeric profile");
  // Channel1 supplies an actual exposed successful response. Channel0 supplies
  // a protected target and a second AW with a nonzero trailing W obligation.
  drive_bready(0);seed_count=b_count[1];
  send_aw(1,108,64,4);inline_payload(1,64,8'h69);
  for(int n=0;n<10000;n++) begin source_negative(1);if(bvalid[1]) break;end
  check(bvalid[1] && b[1].response==0,"ECC boundary successful seed exposure");eb_mark("held");
  target_count=b_count[0];eb_mr43[0]=1;eb_armed=1;
  send_aw(0,110,512,4);inline_payload(0,512,8'hf3);
  send_aw(0,112,768,4);send_w(0,768,4,0,1);eb_trailing=1;
  for(int n=0;n<10000;n++) begin @(negedge clk);if(eb_fault) break;end
  check(eb_fault,"ECC boundary injection deadline");
  for(int n=0;n<100;n++) begin @(negedge clk);if(host_fault && !enable) break;end
  check(host_fault && !enable && !settled,"ECC boundary ownership fault fences and retains host work");
  write_csr(INDEX_CONTROL,8);
  repeat(16) begin
    @(negedge clk);
    check(!reset_request && !settled && bvalid[1] && b[1].response==0,"ECC boundary held B/reset gate");
  end
  // Fatal W drain remains a real source handshake; never discard trailing W.
  send_w(0,768,4,1,4);
  check(dut.late_error,"ECC boundary late-error after exposed successful B");
  source_negative(1);bready[1]=1;wait_b(1,108,seed_count+1);
  source_negative(0);bready[0]=1;while(b_count[0]<target_count+2) source_negative(0);wait_settled();eb_mark("settled");
  for(int n=0;n<100;n++) begin @(negedge clk);if(reset_request) break;end
  check(reset_request,"ECC boundary settled destructive request");
  repeat(8) begin @(negedge clk);check(reset_request && status_value[0].boot_epoch==0,"ECC boundary stale high reset confirmation");end
  management[0].error=0;external_fault=0;eb_mr43=0;reset_confirmed=0;
  repeat(4) @(negedge clk);reset_confirmed='1;
  for(int n=0;n<300;n++) begin @(negedge clk);if(!host_fault && status_value[0].boot_epoch==1) break;end
  check(!host_fault && !enable && !dut.host_config_active && !status_value[0].config_valid
    && status_value[0].boot_epoch==1 && status_value[1].boot_epoch==1,"ECC boundary destructive invalidation");
  eb_armed=0;eb_mark("reset_consumed");
  wait_status(INDEX_BOOT_EPOCH,32'hffffffff,1);
  for(int i=0;i<65536;i++) expected[i]=0;
  publish_and_boot(1);eb_mark("fresh");
  target_count=b_count[0];send_aw(0,120,0,4);inline_payload(0,0,8'ha7);wait_b(0,120,target_count+1);
  send_ar(1,121,0,4);read_result(1,121,0,4);wait_settled();
  check(!host_fault && dut.stale_completions==0,"ECC boundary fresh protected service");
  write_csr(INDEX_CONTROL,2);
  for(int c=0;c<CHANNELS;c++) begin
    for(int n=0;n<10000;n++) begin
      @(negedge clk);if(status_value[c].state==1 && status_value[c].drained) break;
    end
    check(status_value[c].state==1 && status_value[c].drained,"ECC boundary final quiesce tail");
  end
  @(negedge clk);finish_observations=1;repeat(2) @(negedge clk);
  continuous_close();eb_mark("end");$fclose(eb_stream);eb_stream=0;
  $display("CONTROLLER_ECC_BOUNDARY_PASS kind=%0d delta=%0d posted=%0d checks=%0d",eb_kind,eb_delta,eb_posted,checks);
  $display("CONTROLLER_INTEGRATION_PASS ecc_boundary checks=%0d",checks);$finish;
endtask
