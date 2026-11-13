// Timed seam regression of the actual fixture phase selector. The live gate
// is a probe, not the independent physical/thermal/RFM admission oracle.
module wrapper_readiness_tb;
  localparam int CHANNELS=2;
  logic clk=0;
  always #0.625 clk<=~clk;
  logic [1:0] tb_reset_ok='1;
  logic [31:0] tb_boot[CHANNELS],tb_config_epoch[CHANNELS];
  bit [1:0] zq_pending=0,mr4_pending=0,zq_done=0,mr4_done=0,work_pending=0,capture_enable='1;
  int tb_tick[CHANNELS],headroom=100,required_ck=10,checks=0,dirty_words=12;
  string path;
  int stream;
  `include "wrapper_readiness.svh"
  function automatic longint timestamp_ps();
    realtime captured;
    captured=$realtime;return longint'(captured*1000.0);
  endfunction
  function automatic bit clear_now(input int c);
    if(c<0 || c>=CHANNELS) return 0;
    return !zq_pending[c] && !mr4_pending[c] && !work_pending[c];
  endfunction
  function automatic bit selected();
    bit result;
    result=1;
    for(int c=0;c<CHANNELS;c++)
      result &= wm_phase_ready(c,tb_tick[c]-1,timestamp_ps(),tb_reset_ok[c],tb_boot[c],tb_config_epoch[c],
        clear_now(c) && headroom>required_ck);
    return result;
  endfunction
  for(genvar c=0;c<CHANNELS;c++) begin : channel
    initial forever begin @(negedge tb_reset_ok[c]);wm_phase_invalidate(c);end
    initial forever begin @(tb_boot[c] or tb_config_epoch[c]);wm_phase_invalidate(c);end
    initial forever begin
      @(negedge clk);
      wm_phase_record(c,0,tb_tick[c],timestamp_ps(),tb_reset_ok[c],tb_boot[c],tb_config_epoch[c],clear_now(c));
      tb_tick[c]++;
    end
    initial forever begin
      @(posedge clk);
      if(tb_tick[c]>0 && capture_enable[c])
        wm_phase_record(c,1,tb_tick[c]-1,timestamp_ps(),tb_reset_ok[c],tb_boot[c],tb_config_epoch[c],clear_now(c));
    end
    always @(posedge clk) begin
      if(zq_done[c]) begin zq_pending[c]<=0;zq_done[c]<=0;end
      if(mr4_done[c]) begin mr4_pending[c]<=0;mr4_done[c]<=0;end
    end
  end
  task automatic next_marker;
    @(negedge clk);#0.001;
  endtask
  task automatic probe(input string name,input bit expected);
    bit actual;
    actual=selected();checks++;
    $fdisplay(stream,"{\"name\":\"%s\",\"ps\":%0d,\"expected\":%0d,\"actual\":%0d,\"rising_tick0\":%0d,\"rising_tick1\":%0d,\"falling_tick0\":%0d,\"falling_tick1\":%0d}",
      name,timestamp_ps(),expected,actual,wm_phase_rising[0].tick,wm_phase_rising[1].tick,
      wm_phase_falling[0].tick,wm_phase_falling[1].tick);
    assert(actual==expected) else $fatal(1,"readiness %s expected %0d actual %0d",name,expected,actual);
  endtask
  task automatic request_probe(input string name,input bit expected);
    longint before_ps;
    before_ps=timestamp_ps();assert(dirty_words==12) else $fatal;
    probe(name,expected);
    assert(timestamp_ps()==before_ps && dirty_words==12) else $fatal(1,"request deferred");
  endtask
  initial begin
    for(int c=0;c<CHANNELS;c++) begin tb_tick[c]=0;tb_boot[c]=1;tb_config_epoch[c]=2;end
    assert($value$plusargs("observations=%s",path)) else $fatal;
    stream=$fopen(path,"w");assert(stream!=0) else $fatal;
    #0.001;probe("missing_initial_snapshot",0);
    next_marker();zq_pending='1;zq_done='1;
    next_marker();probe("zq_live_clear_previous_pending",0);
    next_marker();probe("zq_first_eligible_pair",1);
    mr4_pending='1;mr4_done='1;
    next_marker();probe("mr4_live_clear_previous_pending",0);
    next_marker();probe("mr4_first_eligible_pair",1);
    capture_enable[1]=0;
    next_marker();probe("channel_skew",0);capture_enable[1]=1;
    next_marker();probe("both_channels_fresh",1);
    wm_phase_invalidate(0);probe("missing_channel_snapshot",0);
    next_marker();probe("new_snapshot_after_invalidation",1);
    capture_enable=0;
    next_marker();probe("stale_both_snapshots",0);capture_enable='1;
    next_marker();probe("fresh_after_stale",1);
    #0.001;probe("wrong_marker_time",0);
    next_marker();probe("before_reset",1);
    tb_reset_ok[0]=0;#0.001;tb_reset_ok[0]=1;#0.001;
    assert(!wm_phase_rising[0].valid && !wm_phase_falling[0].valid) else $fatal(1,"reset pulse cached eligibility");
    probe("reset_invalidated",0);
    next_marker();probe("fresh_after_reset",1);
    tb_boot[1]++;probe("boot_mismatch",0);#0.001;
    assert(!wm_phase_rising[1].valid && !wm_phase_falling[1].valid) else $fatal(1,"tb_boot cache not invalidated");
    next_marker();probe("fresh_after_boot",1);
    tb_config_epoch[0]++;probe("config_mismatch",0);#0.001;
    assert(!wm_phase_rising[0].valid && !wm_phase_falling[0].valid) else $fatal(1,"config cache not invalidated");
    next_marker();probe("fresh_after_config",1);
    headroom=required_ck;probe("current_headroom_equality",0);
    request_probe("dirty_request_headroom_reject_no_delay",0);
    headroom=required_ck+1;request_probe("dirty_request_one_ck_headroom",1);
    @(posedge clk);#0.001;work_pending[1]=1;
    next_marker();probe("new_work_after_eligible_rising",0);
    request_probe("dirty_request_new_work_reject_no_delay",0);
    work_pending=0;probe("falling_pending_cannot_be_replaced_by_live_clear",0);
    next_marker();probe("fresh_after_new_work",1);
    // Current work becoming pending after both snapshots must also veto.
    work_pending[0]=1;request_probe("dirty_request_current_pending",0);work_pending=0;
    request_probe("dirty_request_eligible_exact_trigger",1);
    $fclose(stream);$display("WRAPPER_READINESS_PASS checks=%0d",checks);$finish;
  end
  initial begin #100;$fatal(1,"readiness test timeout");end
endmodule
