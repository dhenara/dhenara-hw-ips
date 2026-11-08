// Scheduler-only fairness evidence. Physical readiness and ownership pauses
// are explicit fixture inputs; this does not qualify the combined PHY path.
module lpddr5_mc_fairness_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0,rst_n=0,request_valid=0,request_ready,launch_valid;
  logic issue_enable=0,launch_ready=1,maintenance_valid=0,maintenance_ready;
  logic drained,pair_pending,fault,pages_closed;
  lpddr5_mc_channel_config_t cfg;
  lpddr5_mc_granule_t request,launch;
  lpddr5_mc_ca_t ca;
  lpddr5_mc_event_t event_value;
  logic sync,sync_write,sync_rank,rows_closed,stop_requested;
  logic [63:0] accepted,issued,replayed,max_wait;
  logic unused_debug;
  int stream,ck=0;
  string trace_path;
  assign unused_debug=^{ca,event_value,sync,sync_write,sync_rank,rows_closed,stop_requested,
    accepted,issued,replayed,max_wait};
  lpddr5_mc_scheduler #(.QUEUE_DEPTH(64)) dut (
    .clk_ck_i(clk),.rst_ni(rst_n),.abort_i(1'b0),.config_i(cfg),
    .admission_i(1'b1),.issue_enable_i(issue_enable),.command_enable_i(1'b1),
    .request_valid_i(request_valid),.request_ready_o(request_ready),.request_i(request),
    .launch_valid_o(launch_valid),.launch_ready_i(launch_ready),.launch_o(launch),
    .ca_o(ca),.event_o(event_value),.sync_o(sync),.sync_write_o(sync_write),.sync_rank_o(sync_rank),
    .wck_disabled_i(1'b1),.wck_reusable_i(1'b0),.wck_rank_i(1'b0),.wck_write_i(1'b0),
    .drained_o(drained),.pair_pending_o(pair_pending),.fault_o(fault),
    .maintenance_valid_i(maintenance_valid),.maintenance_rfm_i(1'b0),.maintenance_rank_i(1'b0),
    .maintenance_all_i(1'b1),.maintenance_pair_i(3'd0),.activate_allowed_i('1),.close_i(1'b0),
    .stop_requested_o(stop_requested),.maintenance_ready_o(maintenance_ready),
    .pages_closed_o(pages_closed),.rows_closed_o(rows_closed),.accepted_o(accepted),
    .issued_o(issued),.replayed_o(replayed),.max_wait_o(max_wait));
  task automatic tick;
    #1;clk=1;#1;clk=0;#1;
    if(rst_n) ck++;
  endtask
  function automatic lpddr5_mc_granule_t descriptor(input int n,mode_value);
    lpddr5_mc_granule_t value;
    value='0;value.tag=64'(n)+64'd1;value.generation=1;
    value.is_write=mode_value==2 ? 1'(n/8) : 1'(mode_value);
    value.is_64_bytes=1'(n/16%2==0);value.byte_enable='1;
    value.port_index=1'(n);value.qos_class=2'(n/2);
    value.bank_group=2'(n/4);value.bank=2'(n);value.column=12'(n/16*2);
    value.address=40'(n)*64;
    return value;
  endfunction
  task automatic run_case(input int divisor_value,mode_value,pause_value);
    int admitted_count,completed_count,pause_reason,request_tag,grant_tag;
    bit maintenance_pending,refresh_seen,refresh_now;
    logic [255:0] seen;
    lpddr5_mc_granule_t expected;
    rst_n=0;request_valid=0;maintenance_valid=0;cfg='0;request='0;tick();rst_n=1;ck=0;
    cfg.profile=fixture_profile(0,0,0,1,0,0);cfg.profile.ranks=1;
    cfg.service_div=3'(divisor_value);cfg.age_tick_div=8;
    cfg.read_urgent_age=32;cfg.write_urgent_age=32;cfg.read_weight=7;cfg.write_weight=1;
    cfg.read_batch_min=1;cfg.write_batch_min=1;cfg.write_batch_max=4;cfg.bw_window=3000;
    admitted_count=0;completed_count=0;seen=0;maintenance_pending=0;refresh_seen=0;
    $fdisplay(stream,"{\"kind\":\"start\",\"divisor\":%0d,\"mode\":%0d,\"pause_mode\":%0d}",divisor_value,mode_value,pause_value);
    for(int cycle=0;cycle<16000;cycle++) begin
      if(pause_value==3 && cycle==400) begin maintenance_pending=1;maintenance_valid=1;end
      issue_enable=cycle>=64 && !maintenance_pending
        && !(pause_value==2 && cycle%500>=200 && cycle%500<237);
      // An accepted access pair owns its reserved physical slot.
      launch_ready=pair_pending || !(pause_value==1 && cycle%500>=200 && cycle%500<237);
      request_valid=admitted_count<256 && admitted_count-completed_count<64;
      request=descriptor(admitted_count,mode_value);
      #1;
      pause_reason=0;
      if(!pair_pending) begin
        if(maintenance_pending) pause_reason=3;
        else if(!issue_enable) pause_reason=2;
        else if(!launch_ready) pause_reason=1;
      end
      request_tag=-1;grant_tag=-1;
      if(request_valid && request_ready) begin request_tag=admitted_count;admitted_count++;end
      if(launch_valid) begin
        grant_tag=int'(launch.tag)-1;
        assert(grant_tag>=0 && grant_tag<admitted_count && !seen[grant_tag]) else $fatal(1,"fairness duplicate/unknown grant");
        expected=descriptor(grant_tag,mode_value);
        assert(launch==expected && launch_ready) else $fatal(1,"fairness grant payload/credit");
        assert(pause_reason==0) else $fatal(1,"grant during suspended opportunity");
        seen[grant_tag]=1;completed_count++;
      end
      $fdisplay(stream,"{\"kind\":\"cycle\",\"ck\":%0d,\"request\":%0d,\"grant\":%0d,\"pause\":%0d}",ck,request_tag,grant_tag,pause_reason);
      refresh_now=maintenance_ready;
      tick();assert(!fault) else $fatal(1,"fairness scheduler fault");
      if(refresh_now) begin maintenance_valid=0;refresh_seen=1;end
      if(refresh_seen && pages_closed) maintenance_pending=0;
      if(completed_count==256) break;
    end
    assert(admitted_count==256 && completed_count==256 && seen=='1 && drained)
      else $fatal(1,"fairness finite progress div=%0d mode=%0d pause=%0d completed=%0d",divisor_value,mode_value,pause_value,completed_count);
    assert(pause_value!=3 || refresh_seen) else $fatal(1,"fairness maintenance stimulus missing");
    $fdisplay(stream,"{\"kind\":\"end\",\"ck\":%0d}",ck);
    $display("FAIRNESS_STREAM_PASS divisor=%0d mode=%0d pause=%0d CK=%0d",divisor_value,mode_value,pause_value,ck);
  endtask
  initial begin
    cfg='0;request='0;
    assert($value$plusargs("fairness_trace=%s",trace_path)) else $fatal(1,"fairness trace required");
    stream=$fopen(trace_path,"w");assert(stream!=0) else $fatal(1,"fairness trace open");
    for(int divisor_value=1;divisor_value<=4;divisor_value*=2)
      for(int mode_value=0;mode_value<3;mode_value++)
        for(int pause_value=0;pause_value<4;pause_value++) run_case(divisor_value,mode_value,pause_value);
    $fclose(stream);$display("PASS fairness streams: 36 actual scheduler workloads");$finish;
  end
endmodule
