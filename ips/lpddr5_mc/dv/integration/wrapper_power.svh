// Reachable automatic-power stimulus on the real wrapper. Only APB, AXI and
// external PHY inputs are driven; no readiness, counter or settlement is forced.
int wp_policy=0;
logic [CHANNELS-1:0] wp_entered=0;
localparam int WP_IDLE_CK=64,WP_HOLD_CK=128;
// A legal PHY returns each low-power acknowledgement after observing the
// actual request. The rising pre-NBA power_edge records the consumed values.
initial forever begin
  @(negedge clk);#0.001;
  if(WRAPPER_MANAGEMENT && wp_policy!=0 && rst_n) for(int c=0;c<CHANNELS;c++) begin
    management[c].lp_ctrl_ack=management_out[c].lp_ctrl_req;
    management[c].lp_data_ack=management_out[c].lp_data_req;
    if(ca[c].cs!=0 && ca[c].address[6:0]==(wp_policy==1 ? 7'd64 : 7'd104)) wp_entered[c]=1;
  end
end
for(genvar c=0;c<CHANNELS;c++) begin : wp_observers
  always @(posedge clk) if(wm_stream!=0 && wp_policy!=0 && wm_tick[c]>0)
    $fdisplay(wm_stream,"{\"kind\":\"power_edge\",\"ps\":%0d,\"tick\":%0d,\"channel\":%0d,\"ctrl_req\":%0d,\"data_req\":%0d,\"ctrl_ack\":%0d,\"data_ack\":%0d,\"ctrlupd_req\":%0d,\"wake\":%0d,\"policy\":%0d,\"idle_ck\":%0d}",
      observer.timestamp_ps(),wm_tick[c]-1,c,management_out[c].lp_ctrl_req,management_out[c].lp_data_req,
      management[c].lp_ctrl_ack,management[c].lp_data_ack,management_out[c].ctrlupd_req,|dut.wake_sync_q,
      dut.datapath.channels[c].channel.active.power_policy,
      wp_policy==1 ? dut.datapath.channels[c].channel.active.power_down_idle_ck : dut.datapath.channels[c].channel.active.self_refresh_idle_ck);
end
task automatic wrapper_power_run;
  int dirty_words;
  bit seen_check;
  check(PORTS==2 && CHANNELS==2 && RANKS==1 && DATA_BITS==128 && PROFILE_NUMBER==0 && SERVICE_DIV==2,
    "wrapper power fixed profile0 shape");
  wm_case=6;wm_failed=0;wm_mark("power_begin");
  drive_bready(0);
  send_aw(1,200,64,4);inline_payload(1,64,8'd200);wm_wait_ready(1);
  send_ar(1,201,64,4);wm_wait_ready(0);
  check(b[1].id==200 && r[1].id==201 && b[1].response==0 && r[1].response==0,
    "power deferral requires real exposed B/R");
  send_aw(0,202,0,4);inline_payload(0,0,8'd202);
  dirty_words=0;seen_check=0;
  for(int n=0;n<4000;n++) begin
    @(negedge clk);#0.001;
    if(dut.datapath.channels[0].channel.inline_ecc.data_committed_q) begin
      seen_check=1;dirty_words+=$countones(writes[0].enable)/2;
      if(dirty_words>=12) break;
    end
  end
  check(seen_check && dirty_words==12 && !settled && !status_value[0].ecc_drained,
    "power deferral missing dirty final check interval");
  wm_mark("power_dirty");
  repeat(WP_HOLD_CK) begin
    @(negedge clk);#0.001;
    check(!settled && !host_fault,"held responses unexpectedly settled/faulted");
    for(int c=0;c<CHANNELS;c++)
      check(!management_out[c].lp_ctrl_req && !management_out[c].lp_data_req,
        "automatic power request before held-source settlement");
  end
  wm_mark("power_deferred");
  wm_held_read(201,0);drive_bready(1);wait_settled();wm_mark("power_source_settled");
  for(int n=0;n<8000;n++) begin
    @(negedge clk);#0.001;
    check(!host_fault,"automatic power entry fault");
    if(status_value[0].state==(wp_policy==1 ? 3 : 4) && status_value[1].state==(wp_policy==1 ? 3 : 4)
        && (&wp_entered) && management_out[0].lp_ctrl_req && management_out[1].lp_ctrl_req) break;
  end
  check(status_value[0].state==(wp_policy==1 ? 3 : 4) && status_value[1].state==(wp_policy==1 ? 3 : 4)
    && (&wp_entered) && !enable && settled,"both real channels must enter selected power state");
  wm_mark("power_sleeping");
  // SR owns retention for a hold exceeding tREFI. Ordinary PD remains short;
  // its external retention bound is never suspended by this fixture.
  // Keep the endpoint in the same post-edge phase as power_sleeping. Ending
  // exactly on negedge would shorten the selected residence by one picosecond.
  repeat(wp_policy==1 ? 40 : 3500) begin @(negedge clk);#0.001;end
  wm_mark("power_wake_offer");
  // send_ar holds the real source VALID until acceptance; the synchronized
  // source VALID wake precedes its handshake, which must follow recovery.
  send_ar(0,204,0,4);read_result(0,204,0,4);
  send_ar(1,205,64,4);read_result(1,205,64,4);wait_settled();
  check(enable && !host_fault,"automatic power wake fresh source service");
  wm_mark("power_preserved");
  // End disabled, preventing a new automatic sleep from truncating the tail.
  write_csr(INDEX_CONTROL,2);
  for(int n=0;n<8000;n++) begin
    @(negedge clk);#0.001;
    check(!host_fault,"power final quiesce fault");
    if(dut.quiesced && settled && status_value[0].drained && status_value[1].drained) break;
  end
  check(dut.quiesced && settled && status_value[0].drained && status_value[1].drained,
    "power final source/pin/ECC/WCK drain");
  wm_mark("power_end");wrapper_stop_observations();
  $display("CONTROLLER_WRAPPER_POWER_PASS policy=%0d",wp_policy);
  $display("CONTROLLER_INTEGRATION_PASS checks=%0d wrapper_power=1",checks);$finish;
endtask
