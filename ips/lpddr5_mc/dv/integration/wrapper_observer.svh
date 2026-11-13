// Explicit phase bridge: memory prepare/sample is at falling CK; the later
// rising CK carries real internal handshakes. Both retain the identical raw
// buses, so the adapter qualifies their mapping rather than borrowing C2 ticks.
int wm_stream=0,wm_tick[CHANNELS],wm_edge_tick[CHANNELS];
string wm_path,wm_profile_sha;
initial begin
  for(int c=0;c<CHANNELS;c++) begin wm_tick[c]=0;wm_edge_tick[c]=0;end
  if(WRAPPER_MANAGEMENT) begin
    check($value$plusargs("wrapper_observations=%s",wm_path),"missing wrapper stream");
    check($value$plusargs("wrapper_profile_sha256=%s",wm_profile_sha),"missing wrapper profile digest");
    wm_stream=$fopen(wm_path,"w");check(wm_stream!=0,"wrapper stream open");
    $fdisplay(wm_stream,"{\"kind\":\"header\",\"version\":1,\"sample_phase\":\"negedge_after_prepare_before_sample\",\"handshake_phase\":\"posedge_before_NBA\",\"ck_ps\":1250,\"channels\":2,\"ranks\":1,\"profile_sha256\":\"%s\"}",wm_profile_sha);
  end
end
task automatic wrapper_memory_sample(input int c);
  // Observation executes synchronously in this helper below through constant
  // generate cases, before memory_model.sample can change its counters.
  if(WRAPPER_MANAGEMENT) begin
    if(c==0) wm_observers[0].sample_pins();
    else wm_observers[1].sample_pins();
  end
endtask
for(genvar c=0;c<CHANNELS;c++) begin : wm_observers
  task automatic sample_pins;
    if(wm_stream!=0 && !finish_observations) begin
      assert(qualified[0]==profile) else $fatal(1,"wrapper fixture profile differs from bound image");
      if(status_value[c].config_valid)
        assert(dut.datapath.channels[c].channel.active.profile==qualified[0])
          else $fatal(1,"wrapper active profile differs from bound image");
      $fdisplay(wm_stream,"{\"kind\":\"sample\",\"ps\":%0d,\"channel\":%0d,\"tick\":%0d,\"reset_n\":%0d,\"memory_reset\":%0d,\"destructive\":%0d,\"confirmed\":%0d,\"reset_request\":%0d,\"settled\":%0d,\"enabled\":%0d,\"host_fault\":%0d,\"fault\":%0d,\"trusted\":%0d,\"owner\":%0d,\"boot\":%0d,\"config\":%0d,\"config_valid\":%0d,\"trained\":%0d,\"training_epoch\":%0d,\"state\":%0d,\"drained\":%0d,\"pin_drained\":%0d,\"ecc_drained\":%0d,\"data_committed\":%0d,\"admission\":%0d,\"phy_update\":%0d,\"master\":%0d,\"rank_valid\":%0d,\"rank_sr\":%0d,\"refresh_request\":%0d,\"refresh_done\":%0d,\"phyupd_req\":%0d,\"phymstr_req\":%0d,\"phyupd_type\":%0d,\"phymstr_type\":%0d,\"phymstr_cs_state\":%0d,\"phymstr_state_sel\":%0d,\"error\":%0d,\"mr4\":%0d,\"mr43\":%0d,\"cs\":%0d,\"ca\":%0d,\"wck\":\"0x%h\",\"write\":\"0x%h\",\"read\":\"0x%h\",\"return\":\"0x%h\",\"profile_image\":\"0x%h\"}",
        observer.timestamp_ps(),c,wm_tick[c],rst_n,memory_reset,dut.datapath.channels[c].channel.destructive_reset,reset_confirmed[c],
        reset_request,settled,enable,host_fault,status_value[c].fault,status_value[c].memory_trusted,status_value[c].owner,
        status_value[c].boot_epoch,status_value[c].config_epoch,status_value[c].config_valid,status_value[c].train_done,management[c].training_epoch,status_value[c].state,status_value[c].drained,
        status_value[c].pin_drained,status_value[c].ecc_drained,dut.datapath.channels[c].channel.inline_ecc.data_committed_q,dut.datapath.channels[c].channel.admission,management_out[c].phyupd_ack,management_out[c].phymstr_ack,report_valid[c],
        report_sr[c],management_out[c].refresh_request,phy_refresh_done[c],management[c].phyupd_req,management[c].phymstr_req,management[c].phyupd_type,management[c].phymstr_type,
        management[c].phymstr_cs_state,management[c].phymstr_state_sel,management[c].error,mr4_value[c],8'd0,ca[c].cs,ca[c].address,
        wck[c],writes[c],reads[c],returns[c],qualified[0]);
      wm_tick[c]++;
    end
  endtask
  always @(posedge clk) if(wm_stream!=0 && wm_tick[c]>wm_edge_tick[c]) begin
    wm_edge_tick[c]<=wm_tick[c];
    $fdisplay(wm_stream,"{\"kind\":\"edge\",\"ps\":%0d,\"channel\":%0d,\"tick\":%0d,\"cs\":%0d,\"ca\":%0d,\"wck\":\"0x%h\",\"write\":\"0x%h\",\"read\":\"0x%h\",\"return\":\"0x%h\"}",
      observer.timestamp_ps(),c,wm_tick[c]-1,ca[c].cs,ca[c].address,wck[c],writes[c],reads[c],returns[c]);
    if(rst_n && !dut.datapath.channels[c].channel.destructive_reset && dut.datapath.channels[c].channel.active.inline_ecc) begin
      if(dut.datapath.channels[c].channel.inline_ecc.request_valid_i && dut.datapath.channels[c].channel.inline_ready)
        wm_granule("inline_admit",c,dut.datapath.channels[c].channel.request_i);
      if(dut.datapath.channels[c].channel.inline_physical_valid && dut.datapath.channels[c].channel.inline_physical_ready)
        wm_granule("inline_physical",c,dut.datapath.channels[c].channel.inline_physical);
      if(dut.datapath.channels[c].channel.inline_ecc.result_valid_i && dut.datapath.channels[c].channel.inline_result_ready)
        wm_result("inline_result",c,dut.datapath.channels[c].channel.backend_completion);
      if(dut.datapath.channels[c].channel.inline_completion_valid && dut.datapath.channels[c].channel.completion_ready_i)
        wm_result("inline_complete",c,dut.datapath.channels[c].channel.inline_completion);
    end
  end
end
task automatic wm_granule(input string kind,input int channel,input lpddr5_mc_granule_t g);
  $fdisplay(wm_stream,"{\"kind\":\"%s\",\"tick\":%0d,\"ps\":%0d,\"channel\":%0d,\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"rank\":%0d,\"row\":%0d,\"bank\":%0d,\"column\":%0d,\"write\":%0d,\"bytes\":%0d,\"mask\":\"0x%h\",\"data\":\"0x%h\"}",
    kind,wm_tick[channel]-1,observer.timestamp_ps(),channel,g.tag,g.generation,status_value[channel].boot_epoch,status_value[channel].config_epoch,
    g.rank_index,g.row,{g.bank_group,g.bank},g.column,g.is_write,g.is_64_bytes ? 64 : 32,g.byte_enable,g.data);
endtask
task automatic wm_result(input string kind,input int channel,input lpddr5_mc_channel_completion_t v);
  $fdisplay(wm_stream,"{\"kind\":\"%s\",\"tick\":%0d,\"ps\":%0d,\"channel\":%0d,\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"write\":%0d,\"physical\":%0d,\"integrity\":%0d,\"failed\":%0d,\"response\":%0d,\"data\":\"0x%h\"}",
    kind,wm_tick[channel]-1,observer.timestamp_ps(),channel,v.identity.tag,v.identity.generation,v.identity.boot_epoch,v.identity.config_epoch,
    v.is_write,v.physical_done,v.integrity_done,v.failed,v.response,v.data);
endtask
final begin
  if(wm_stream!=0) begin
    $fdisplay(wm_stream,"{\"kind\":\"end\",\"ps\":%0d}",observer.timestamp_ps());$fclose(wm_stream);
  end
end
