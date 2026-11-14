// Current combinational failure decision and sampled ages/obligations are
// diagnostic witnesses. first_fault deliberately survives destructive recovery.
// The offline window verdict instead uses decoded CA and complete pin returns.
longint signed wm_ref_tick[CHANNELS],wm_mrr_tick[CHANNELS],wm_zq_tick[CHANNELS];
bit wm_mr4_complete[CHANNELS],wm_setup_live=0;
`include "wrapper_readiness.svh"
// Evaluate at the marker's actual +1ps boundary, not in a continuous assign
// that cannot be awakened by the passage of simulation time alone.
function automatic bit wm_ready_window(input int required_ck,input bit idle);
  return wm_observers[0].ready(required_ck,idle) && wm_observers[1].ready(required_ck,idle);
endfunction
longint unsigned wm_setup_ps=0;
initial for(int c=0;c<CHANNELS;c++) begin
  wm_ref_tick[c]=-1;wm_mrr_tick[c]=-1;wm_zq_tick[c]=-1;wm_mr4_complete[c]=0;
end
// This watchdog cannot defer the dirty request or restart a failed setup.
initial forever begin
  @(negedge clk);
  if(WRAPPER_MANAGEMENT && wm_setup_live)
    check(observer.timestamp_ps()-wm_setup_ps<=64'(WM_WINDOW_SETUP_CK)*1250,"all priming/setup budget exhausted");
end
// Explicit phase bridge: memory prepare/sample is at falling CK; the later
// rising CK carries real internal handshakes. Both retain the identical raw
// buses, so the adapter qualifies their mapping rather than borrowing C2 ticks.
// Capture returns from the memory's procedural output immediately after
// prepare. Its propagated integration net still has the prior value in this
// delta cycle; the later edge records the actual receiver input independently.
int wm_stream=0,wm_tick[CHANNELS],wm_edge_tick[CHANNELS];
string wm_path,wm_profile_sha;
initial begin
  for(int c=0;c<CHANNELS;c++) begin wm_tick[c]=0;wm_edge_tick[c]=0;end
  if(WRAPPER_MANAGEMENT) begin
    check($value$plusargs("wrapper_observations=%s",wm_path),"missing wrapper stream");
    check($value$plusargs("wrapper_profile_sha256=%s",wm_profile_sha),"missing wrapper profile digest");
    if(!$value$plusargs("wrapper_case=%d",wm_selected_case)) wm_selected_case=-1;
    wm_stream=$fopen(wm_path,"w");check(wm_stream!=0,"wrapper stream open");
    $fdisplay(wm_stream,"{\"kind\":\"header\",\"version\":7,\"selected_case\":%0d,\"recovery_phase\":\"registered_reset_next_rising_then_falling_v1\",\"maintenance_phase\":\"falling_and_rising_before_NBA\",\"host_phase\":\"posedge_before_NBA_next_edge_counter\",\"host_reset_phase\":\"negedge_core_reset_before_NBA\",\"sample_phase\":\"negedge_after_prepare_before_sample\",\"return_source\":\"memory_output_after_prepare\",\"handshake_phase\":\"posedge_before_NBA\",\"management_phase\":\"posedge_before_NBA\",\"ck_ps\":1250,\"channels\":2,\"ranks\":1,\"profile_sha256\":\"%s\"}",wm_selected_case,wm_profile_sha);
  end
end
task automatic wrapper_memory_sample(input int c,input lpddr5_mc_return4_t prepared_return);
  // Observation executes synchronously in this helper below through constant
  // generate cases, before memory_model.sample can change its counters.
  if(WRAPPER_MANAGEMENT) begin
    if(c==0) wm_observers[0].sample_pins(prepared_return);
    else wm_observers[1].sample_pins(prepared_return);
  end
endtask
task automatic wm_capture_address(input int p);
    $fdisplay(wm_stream,"{\"kind\":\"address\",\"ps\":%0d,\"port\":%0d,\"awvalid\":%0d,\"awready\":%0d,\"arvalid\":%0d,\"arready\":%0d,\"aw_bits\":\"0x%h\",\"ar_bits\":\"0x%h\",\"aw_id\":%0d,\"ar_id\":%0d,\"aw_address\":\"0x%h\",\"ar_address\":\"0x%h\"}",
      observer.timestamp_ps(),p,awvalid[p],awready[p],arvalid[p],arready[p],aw[p],ar[p],aw[p].id,ar[p].id,aw[p].address,ar[p].address);
    if(wm_case>=0 && (management[0].phyupd_req || management[0].phymstr_req) && !enable
        && awvalid[p] && arvalid[p] && !awready[p] && !arready[p]) wm_probe_pending[p]=1;
endtask
for(genvar p=0;p<PORTS;p++) begin : wm_sources
  assert property(@(posedge axi_clk[p]) disable iff(!monitor_rst_n[p] || !WRAPPER_MANAGEMENT)
    awvalid[p]&&!awready[p] |=> awvalid[p]&&$stable(aw[p]));
  assert property(@(posedge axi_clk[p]) disable iff(!monitor_rst_n[p] || !WRAPPER_MANAGEMENT)
    arvalid[p]&&!arready[p] |=> arvalid[p]&&$stable(ar[p]));
end
for(genvar c=0;c<CHANNELS;c++) begin : wm_observers
  `include "wrapper_maintenance_observer.svh"
  task automatic sample_pins(input lpddr5_mc_return4_t prepared_return);
    if(wm_accept_sample()) begin
      $fdisplay(wm_stream,"{\"kind\":\"physical_ca\",\"ps\":%0d,\"channel\":%0d,\"tick\":%0d,\"cs\":%0d,\"ca\":%0d,\"phy\":%0d}",
        observer.timestamp_ps(),c,wm_tick[c],wm_pin_ca[c].cs,wm_pin_ca[c].address,management_out[c].phymstr_ack);
      assert(qualified[0]==profile) else $fatal(1,"wrapper fixture profile differs from bound image");
      if(status_value[c].config_valid)
        assert(dut.datapath.channels[c].channel.active.profile==qualified[0])
          else $fatal(1,"wrapper active profile differs from bound image");
      $fdisplay(wm_stream,"{\"kind\":\"sample\",\"ps\":%0d,\"channel\":%0d,\"tick\":%0d,\"reset_n\":%0d,\"memory_reset\":%0d,\"destructive\":%0d,\"confirmed\":%0d,\"reset_request\":%0d,\"settled\":%0d,\"enabled\":%0d,\"host_fault\":%0d,\"fault\":%0d,\"trusted\":%0d,\"owner\":%0d,\"boot\":%0d,\"config\":%0d,\"config_valid\":%0d,\"trained\":%0d,\"training_epoch\":%0d,\"state\":%0d,\"drained\":%0d,\"pin_drained\":%0d,\"ecc_drained\":%0d,\"data_committed\":%0d,\"admission\":%0d,\"phy_update\":%0d,\"master\":%0d,\"rank_valid\":%0d,\"rank_sr\":%0d,\"refresh_request\":%0d,\"refresh_done\":%0d,\"phyupd_req\":%0d,\"phymstr_req\":%0d,\"phyupd_type\":%0d,\"phymstr_type\":%0d,\"phymstr_cs_state\":%0d,\"phymstr_state_sel\":%0d,\"error\":%0d,\"mr4\":%0d,\"mr43\":%0d,\"cs\":%0d,\"ca\":%0d,\"wck\":\"0x%h\",\"write\":\"0x%h\",\"read\":\"0x%h\",\"return\":\"0x%h\",\"profile_image\":\"0x%h\",\"prepared_valid\":%0d,\"train_request\":%0d,\"issue\":%0d,\"reset_authorized\":%0d,\"recover_command\":%0d}",
        observer.timestamp_ps(),c,wm_tick[c],rst_n,memory_reset,dut.datapath.channels[c].channel.destructive_reset,reset_confirmed[c],
        reset_request,settled,enable,host_fault,status_value[c].fault,status_value[c].memory_trusted,status_value[c].owner,
        status_value[c].boot_epoch,status_value[c].config_epoch,status_value[c].config_valid,status_value[c].train_done,management[c].training_epoch,status_value[c].state,status_value[c].drained,
        status_value[c].pin_drained,status_value[c].ecc_drained,dut.datapath.channels[c].channel.inline_ecc.data_committed_q,dut.datapath.channels[c].channel.admission,management_out[c].phyupd_ack,management_out[c].phymstr_ack,report_valid[c],
        report_sr[c],management_out[c].refresh_request,phy_refresh_done[c],management[c].phyupd_req,management[c].phymstr_req,management[c].phyupd_type,management[c].phymstr_type,
        management[c].phymstr_cs_state,management[c].phymstr_state_sel,management[c].error,mr4_value[c],8'd0,ca[c].cs,ca[c].address,
        wck[c],writes[c],reads[c],prepared_return,qualified[0],
      dut.datapath.channels[c].channel.prepared_valid_o,status_value[c].train_request,dut.datapath.channels[c].channel.issue_enable,dut.datapath.channels[c].channel.reset_confirmed_i,(dut.datapath.channels[c].channel.command_valid_i && dut.datapath.channels[c].channel.command_i==CH_CMD_RECOVER));
      observe_commands();
      snapshot("window_sample",wm_tick[c]);
      wm_tick[c]++;
    end
  endtask
  task automatic tail_diagnostic;
    $display("WM_TAIL_TIMEOUT channel=%0d aggregate=%0d host=%0d backend=%0d inline=%0d pages=%0d rows=%0d wck_disabled=%0d register=%0d maintenance=%0d thermal=%0d ref_budget=%0d wck_tick=%0d disable=%0d post=%0d scheduler_ck=%0d open=%h",
      c,status_value[c].drained,settled,dut.datapath.channels[c].channel.backend_drained,
      dut.datapath.channels[c].channel.inline_drained,dut.datapath.channels[c].channel.pages_closed,
      dut.datapath.channels[c].channel.rows_closed,dut.datapath.channels[c].channel.wck_disabled,
      dut.datapath.channels[c].channel.register_state_q,dut.datapath.channels[c].channel.maintenance_urgent,
      dut.datapath.channels[c].channel.thermal,dut.datapath.channels[c].channel.refresh_budget,
      dut.datapath.channels[c].channel.backend.wck.tick_q,dut.datapath.channels[c].channel.backend.wck.disable_q,
      dut.datapath.channels[c].channel.backend.wck.post_q,dut.datapath.channels[c].channel.backend.scheduler.ck_q,
      dut.datapath.channels[c].channel.backend.scheduler.open_q);
    for(int bank_index=0;bank_index<32;bank_index++) if(dut.datapath.channels[c].channel.backend.scheduler.act_ready_q[bank_index]
        >dut.datapath.channels[c].channel.backend.scheduler.ck_q)
      $display("WM_TAIL_BANK channel=%0d bank=%0d act_ready=%0d",c,bank_index,
        dut.datapath.channels[c].channel.backend.scheduler.act_ready_q[bank_index]);
  endtask
  task automatic capture_edge;
    $fdisplay(wm_stream,"{\"kind\":\"edge\",\"ps\":%0d,\"channel\":%0d,\"tick\":%0d,\"cs\":%0d,\"ca\":%0d,\"wck\":\"0x%h\",\"write\":\"0x%h\",\"read\":\"0x%h\",\"return\":\"0x%h\"}",
      observer.timestamp_ps(),c,wm_tick[c]-1,ca[c].cs,ca[c].address,wck[c],writes[c],reads[c],returns[c]);
    // Live rising-edge inputs and registered outputs, read before NBA.
    // This separate record must never be synthesized from the falling record.
    $fdisplay(wm_stream,"{\"kind\":\"management_edge\",\"ps\":%0d,\"channel\":%0d,\"tick\":%0d,\"reset_n\":%0d,\"memory_reset\":%0d,\"destructive\":%0d,\"fault\":%0d,\"owner\":%0d,\"state\":%0d,\"settled\":%0d,\"drained\":%0d,\"pin_drained\":%0d,\"ecc_drained\":%0d,\"phy_update\":%0d,\"master\":%0d,\"phyupd_req\":%0d,\"phymstr_req\":%0d,\"phyupd_type\":%0d,\"phymstr_type\":%0d,\"phymstr_cs_state\":%0d,\"phymstr_state_sel\":%0d,\"rank_valid\":%0d,\"rank_sr\":%0d,\"refresh_request\":%0d,\"refresh_done\":%0d,\"boot\":%0d,\"config\":%0d,\"prepared_valid\":%0d,\"train_request\":%0d,\"issue\":%0d,\"reset_authorized\":%0d,\"recover_command\":%0d,\"config_valid\":%0d,\"trained\":%0d,\"trusted\":%0d,\"enabled\":%0d,\"admission\":%0d,\"confirmed\":%0d,\"reset_request\":%0d,\"training_epoch\":%0d}",
      observer.timestamp_ps(),c,wm_tick[c]-1,rst_n,memory_reset,dut.datapath.channels[c].channel.destructive_reset,status_value[c].fault,status_value[c].owner,
      status_value[c].state,settled,status_value[c].drained,status_value[c].pin_drained,status_value[c].ecc_drained,
      management_out[c].phyupd_ack,management_out[c].phymstr_ack,management[c].phyupd_req,management[c].phymstr_req,management[c].phyupd_type,
      management[c].phymstr_type,management[c].phymstr_cs_state,management[c].phymstr_state_sel,report_valid[c],report_sr[c],
      management_out[c].refresh_request,phy_refresh_done[c],status_value[c].boot_epoch,status_value[c].config_epoch,
      dut.datapath.channels[c].channel.prepared_valid_o,status_value[c].train_request,dut.datapath.channels[c].channel.issue_enable,dut.datapath.channels[c].channel.reset_confirmed_i,(dut.datapath.channels[c].channel.command_valid_i && dut.datapath.channels[c].channel.command_i==CH_CMD_RECOVER),status_value[c].config_valid,status_value[c].train_done,status_value[c].memory_trusted,enable,dut.datapath.channels[c].channel.admission,reset_confirmed[c],reset_request,management[c].training_epoch);
    snapshot("window_edge",wm_tick[c]-1);
    if(rst_n && !dut.datapath.channels[c].channel.destructive_reset && dut.datapath.channels[c].channel.active.inline_ecc) begin
      if(dut.datapath.channels[c].channel.inline_ecc.request_valid_i && dut.datapath.channels[c].channel.inline_ready)
        wm_granule("inline_admit",c,dut.datapath.channels[c].channel.request_i,dut.datapath.request_boot[c],dut.datapath.request_epoch[c]);
      if(dut.datapath.channels[c].channel.inline_physical_valid && dut.datapath.channels[c].channel.inline_physical_ready)
        wm_granule("inline_physical",c,dut.datapath.channels[c].channel.inline_physical,status_value[c].boot_epoch,status_value[c].config_epoch);
      if(dut.datapath.channels[c].channel.inline_ecc.result_valid_i && dut.datapath.channels[c].channel.inline_result_ready)
        wm_result("inline_result",c,dut.datapath.channels[c].channel.backend_completion);
      if(dut.datapath.channels[c].channel.inline_completion_valid && dut.datapath.channels[c].channel.completion_ready_i)
        wm_result("inline_complete",c,dut.datapath.channels[c].channel.inline_completion);
    end
  endtask
end
task automatic wm_granule(input string kind,input int channel,input lpddr5_mc_granule_t g,input logic [31:0] boot,epoch);
  $fdisplay(wm_stream,"{\"kind\":\"%s\",\"tick\":%0d,\"ps\":%0d,\"channel\":%0d,\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"rank\":%0d,\"row\":%0d,\"bank\":%0d,\"column\":%0d,\"write\":%0d,\"bytes\":%0d,\"mask\":\"0x%h\",\"data\":\"0x%h\"}",
    kind,wm_tick[channel]-1,observer.timestamp_ps(),channel,g.tag,g.generation,boot,epoch,
    g.rank_index,g.row,{g.bank_group,g.bank},g.column,g.is_write,g.is_64_bytes ? 64 : 32,g.byte_enable,g.data);
endtask
task automatic wm_result(input string kind,input int channel,input lpddr5_mc_channel_completion_t v);
  $fdisplay(wm_stream,"{\"kind\":\"%s\",\"tick\":%0d,\"ps\":%0d,\"channel\":%0d,\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"write\":%0d,\"physical\":%0d,\"integrity\":%0d,\"failed\":%0d,\"response\":%0d,\"data\":\"0x%h\"}",
    kind,wm_tick[channel]-1,observer.timestamp_ps(),channel,v.identity.tag,v.identity.generation,v.identity.boot_epoch,v.identity.config_epoch,
    v.is_write,v.physical_done,v.integrity_done,v.failed,v.response,v.data);
endtask
`include "wrapper_host_observer.svh"
// The shared capture skeleton is also executed by wrapper_capture_tb.sv.
wire wm_core_reset_n=dut.datapath.host.core_reset_n;
`include "wrapper_capture.svh"
task automatic wm_close_capture;
  assert(observer.stream==0) else $fatal(1,"source stream did not close after final CK");
  $fdisplay(wm_stream,"{\"kind\":\"end\",\"ps\":%0d}",observer.timestamp_ps());
  $fclose(wm_stream);wm_stream=0;
endtask
final begin
  // An unexpected simulation exit must not manufacture a complete tail.
  if(wm_stream!=0) begin
    $fdisplay(wm_stream,"{\"kind\":\"incomplete\",\"ps\":%0d}",observer.timestamp_ps());$fclose(wm_stream);
  end
end
