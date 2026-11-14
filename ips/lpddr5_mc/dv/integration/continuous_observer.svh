// Opt-in continuous evidence. Unlike the segmented payload observers, this
// samples every CK through reset/fault/ownership loss to its paired final cutoff.
// Internal handshakes are recorded as such, alongside the unmodified pins;
// neither an empty flag nor an internal result is a physical-memory proof.
int continuous_stream=0,continuous_ck=0,continuous_stop_ck=-1;
string continuous_path,continuous_profile_sha256;
initial begin
  if($value$plusargs("continuous_observations=%s",continuous_path)) begin
    continuous_stream=$fopen(continuous_path,"w");
    assert(continuous_stream!=0) else $fatal(1,"cannot open continuous observation trace");
    if(INLINE_LIFECYCLE) begin
      assert($value$plusargs("continuous_profile_sha256=%s",continuous_profile_sha256))
        else $fatal(1,"missing continuous physical profile binding");
      $fdisplay(continuous_stream,"{\"kind\":\"header\",\"version\":2,\"scope\":\"%s\",\"phase\":\"negedge_memory_after_prepare_before_sample\",\"receiving_phase\":\"posedge_before_nba\",\"channels\":%0d,\"ranks\":%0d,\"profile_number\":%0d,\"profile_sha256\":\"%s\",\"external_flips\":%0d}",ECC_BOUNDARY ? string'("ecc_boundary") : string'("inline_lifecycle"),CHANNELS,RANKS,PROFILE_NUMBER,continuous_profile_sha256,ECC_BOUNDARY ? 0 : 1);
    end else
      $fdisplay(continuous_stream,"{\"kind\":\"header\",\"version\":1,\"channels\":%0d,\"ranks\":%0d}",CHANNELS,RANKS);
  end
end
always @(posedge clk) continuous_ck<=continuous_ck+1;
for(genvar cc=0;cc<CHANNELS;cc++) begin : continuous
  always @(posedge clk) if(continuous_stream!=0 && (continuous_stop_ck<0 || continuous_ck<continuous_stop_ck)) begin
    $fdisplay(continuous_stream,"{\"kind\":\"transition\",\"ck\":%0d,\"ps\":%0d,\"channel\":%0d,\"reset_n\":%0d,\"memory_reset\":%0d,\"destructive\":%0d,\"confirmed\":%0d,\"settled\":%0d,\"enabled\":%0d,\"host_fault\":%0d,\"fault\":%0d,\"trusted\":%0d,\"owner\":%0d,\"boot\":%0d,\"config\":%0d,\"config_valid\":%0d,\"trained\":%0d,\"training_epoch\":%0d,\"drained\":%0d,\"pin_drained\":%0d,\"ecc_drained\":%0d,\"inline_idle\":%0d,\"inline_dirty\":%0d,\"command_valid\":%0d,\"command\":%0d,\"update\":%0d,\"phy_update\":%0d,\"fsp\":%0d,\"fsp_busy\":%0d,\"power\":%0d,\"master\":%0d,\"refresh_request\":%0d,\"refresh_done\":%0d,\"rank_valid\":%0d,\"rank_sr\":%0d,\"thermal\":%0d,\"ratio\":%0d,\"cs\":%0d,\"ca\":%0d,\"wck\":\"0x%h\",\"write\":\"0x%h\",\"read\":\"0x%h\",\"return\":\"0x%h\"}",
      continuous_ck,observer.timestamp_ps(),cc,rst_n,memory_reset,dut.datapath.channels[cc].channel.destructive_reset,
      reset_confirmed[cc],settled,enable,host_fault,status_value[cc].fault,status_value[cc].memory_trusted,status_value[cc].owner,
      status_value[cc].boot_epoch,status_value[cc].config_epoch,status_value[cc].config_valid,status_value[cc].train_done,
      management[cc].training_epoch,status_value[cc].drained,status_value[cc].pin_drained,status_value[cc].ecc_drained,
      dut.datapath.channels[cc].channel.inline_drained,dut.datapath.channels[cc].channel.inline_ecc.data_committed_q,
      dut.datapath.channels[cc].channel.command_valid_i,dut.datapath.channels[cc].channel.command_i,
      management_out[cc].ctrlupd_req,management_out[cc].phyupd_ack,management_out[cc].init_start,dut.datapath.channels[cc].channel.fsp_busy,
      management_out[cc].lp_ctrl_req || management_out[cc].lp_data_req,management_out[cc].phymstr_ack,
      management_out[cc].refresh_request,phy_refresh_done[cc],report_valid[cc],report_sr[cc],
      dut.datapath.channels[cc].channel.temperature,dut.datapath.channels[cc].channel.effective_profile.ratio,ca[cc].cs,ca[cc].address,wck[cc],writes[cc],reads[cc],returns[cc]);
    if(rst_n && dut.datapath.channels[cc].channel.active.inline_ecc) begin
      if(dut.datapath.channels[cc].channel.inline_ecc.request_valid_i && dut.datapath.channels[cc].channel.inline_ready)
        continuous_granule("inline_admit",cc,dut.datapath.channels[cc].channel.request_i);
      if(dut.datapath.channels[cc].channel.inline_physical_valid && dut.datapath.channels[cc].channel.inline_physical_ready)
        continuous_granule("inline_physical",cc,dut.datapath.channels[cc].channel.inline_physical);
      if(dut.datapath.channels[cc].channel.inline_ecc.result_valid_i && dut.datapath.channels[cc].channel.inline_result_ready)
        continuous_result("inline_result",cc,dut.datapath.channels[cc].channel.backend_completion);
      if(dut.datapath.channels[cc].channel.inline_completion_valid && dut.datapath.channels[cc].channel.completion_ready_i)
        continuous_result("inline_complete",cc,dut.datapath.channels[cc].channel.inline_completion);
    end
  end
end
// Keep the current preparation and its real receiving edge, then stop before
// the following preparation. The final task may run in either negedge ordering.
// This cutoff closes the observation window, not the memory fixture itself.
task automatic continuous_close;
  if(continuous_stream!=0 && INLINE_LIFECYCLE) begin
    continuous_stop_ck=continuous_ck+1;
    @(posedge clk);@(negedge clk);
  end
endtask
// Called directly on the memory procedural output, before sample(). Reading
// returns[channel] at the same negedge can observe stale port propagation.
task automatic continuous_memory_sample(input int channel,input lpddr5_mc_return4_t value,input logic reset_applied,input logic destructive);
  if(continuous_stream!=0 && INLINE_LIFECYCLE && (continuous_stop_ck<0 || continuous_ck<continuous_stop_ck))
    $fdisplay(continuous_stream,"{\"kind\":\"prepared\",\"ck\":%0d,\"ps\":%0d,\"channel\":%0d,\"reset_applied\":%0d,\"reset_n\":%0d,\"destructive\":%0d,\"mr4\":%0d,\"mr43\":%0d,\"cs\":%0d,\"ca\":%0d,\"wck\":\"0x%h\",\"write\":\"0x%h\",\"read\":\"0x%h\",\"return\":\"0x%h\"}",
      continuous_ck,observer.timestamp_ps(),channel,reset_applied,rst_n,
      destructive,mr4_value[channel],eb_mr43[channel],ca[channel].cs,ca[channel].address,wck[channel],writes[channel],reads[channel],value);
endtask
task automatic continuous_memory_flip(input int channel,input longint unsigned address,input byte unsigned mask);
  if(continuous_stream!=0 && INLINE_LIFECYCLE && (continuous_stop_ck<0 || continuous_ck<continuous_stop_ck))
    $fdisplay(continuous_stream,"{\"kind\":\"memory_flip\",\"id\":0,\"ck\":%0d,\"ps\":%0d,\"channel\":%0d,\"rank\":0,\"address\":%0d,\"mask\":%0d}",
      continuous_ck,observer.timestamp_ps(),channel,address,mask);
endtask
task automatic continuous_granule(input string kind,input int channel,input lpddr5_mc_granule_t g);
  // This evidence projection intentionally omits the remaining packed fields.
  logic unused_g;
  unused_g=^g;
  $fdisplay(continuous_stream,"{\"kind\":\"%s\",\"ck\":%0d,\"channel\":%0d,\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"rank\":%0d,\"row\":%0d,\"bank\":%0d,\"column\":%0d,\"write\":%0d,\"bytes\":%0d,\"mask\":\"0x%h\",\"data\":\"0x%h\"}",
    kind,continuous_ck,channel,g.tag,g.generation,status_value[channel].boot_epoch,status_value[channel].config_epoch,
    g.rank_index,g.row,{g.bank_group,g.bank},g.column,g.is_write,g.is_64_bytes ? 64 : 32,g.byte_enable,g.data);
endtask
task automatic continuous_result(input string kind,input int channel,input lpddr5_mc_channel_completion_t v);
  // This evidence projection intentionally omits the remaining packed fields.
  logic unused_v;
  unused_v=^v;
  $fdisplay(continuous_stream,"{\"kind\":\"%s\",\"ck\":%0d,\"channel\":%0d,\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"write\":%0d,\"physical\":%0d,\"integrity\":%0d,\"failed\":%0d,\"response\":%0d,\"data\":\"0x%h\"}",
    kind,continuous_ck,channel,v.identity.tag,v.identity.generation,v.identity.boot_epoch,v.identity.config_epoch,
    v.is_write,v.physical_done,v.integrity_done,v.failed,v.response,v.data);
endtask
final begin
  if(continuous_stream!=0) begin
    if(INLINE_LIFECYCLE)
      $fdisplay(continuous_stream,"{\"kind\":\"end\",\"ck\":%0d,\"physical_closed\":%0d}",continuous_stop_ck,continuous_stop_ck>=0);
    else $fdisplay(continuous_stream,"{\"kind\":\"end\",\"ck\":%0d}",continuous_ck);
    $fclose(continuous_stream);
  end
end
