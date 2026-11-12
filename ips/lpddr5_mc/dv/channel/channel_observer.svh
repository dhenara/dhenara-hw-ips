// Ordered by the bench after prepare() and before sample()/the clock edge.
// A tick is a logical CK; the variable-delay bench timestamp is kept verbatim.
int c2_stream=0,c2_tick=0,c2_case=-1;
bit c2_complete=0;
string c2_path,c2_profile_sha256;
lpddr5_mc_profile_t c2_expected_profile;
function automatic longint unsigned c2_timestamp_ps;
  return longint'($realtime/1ps);
endfunction
task automatic c2_open;
  if($value$plusargs("channel_observations=%s",c2_path)) begin
    assert($value$plusargs("channel_profile_sha256=%s",c2_profile_sha256)) else $fatal(1,"missing channel profile binding");
    c2_stream=$fopen(c2_path,"w");
    assert(c2_stream!=0) else $fatal(1,"cannot open channel observations");
    $fdisplay(c2_stream,"{\"kind\":\"header\",\"version\":2,\"phase\":\"memory_sample_after_prepare_before_sample_before_clk\",\"time_unit\":\"ps\",\"timing\":\"one_tick_one_profile_CK\",\"handshake_phase\":\"posedge_clk_after_memory_sample\",\"profile_binding\":\"dirty-management-v1\",\"profile_sha256\":\"%s\",\"cases\":8}",c2_profile_sha256);
  end
endtask
task automatic c2_begin(input int kind,input bit failed);
  if(c2_stream!=0) begin
    c2_case=kind*2+int'(failed);
    $fdisplay(c2_stream,"{\"kind\":\"case_begin\",\"case_id\":%0d,\"tick\":%0d,\"management\":%0d,\"failed\":%0d}",c2_case,c2_tick,kind,failed);
  end
endtask
task automatic c2_end;
  if(c2_stream!=0) $fdisplay(c2_stream,"{\"kind\":\"case_end\",\"case_id\":%0d,\"tick\":%0d}",c2_case,c2_tick);
endtask
task automatic c2_sample;
  if(c2_stream!=0) begin
    if(rst_n && !memory_reset) begin
      c2_expected_profile=fixture_profile(memory_profile.ratio==2,0,0,1,0,1);
      c2_expected_profile.ranks=1;
      assert(memory_profile==c2_expected_profile) else $fatal(1,"unbound channel profile image");
    end
$fdisplay(c2_stream,"{\"kind\":\"tick\",\"tick\":%0d,\"case_id\":%0d,\"cycles\":%0d,\"time_ps\":%0d,\"profile\":%0d,\"reset_n\":%0d,\"memory_reset\":%0d,\"destructive\":%0d,\"confirmed\":%0d,\"settled\":%0d,\"enabled\":%0d,\"host_fault\":%0d,\"fault\":%0d,\"trusted\":%0d,\"owner\":%0d,\"boot\":%0d,\"config\":%0d,\"config_valid\":%0d,\"trained\":%0d,\"training_epoch\":%0d,\"drained\":%0d,\"pin_drained\":%0d,\"ecc_drained\":%0d,\"inline_idle\":%0d,\"data_committed\":%0d,\"command_valid\":%0d,\"command_ready\":%0d,\"command\":%0d,\"command_rejected\":%0d,\"update\":%0d,\"phy_update\":%0d,\"fsp\":%0d,\"fsp_busy\":%0d,\"fsp_ready\":%0d,\"power\":%0d,\"master\":%0d,\"refresh_request\":%0d,\"refresh_done\":%0d,\"rank_valid\":%0d,\"rank_sr\":%0d,\"thermal\":%0d,\"ratio\":%0d,\"cs\":%0d,\"ca\":%0d,\"wck\":\"0x%h\",\"write\":\"0x%h\",\"read\":\"0x%h\",\"return\":\"0x%h\",\"mr4\":%0d,\"mr43\":%0d,\"management_in\":\"0x%h\",\"management_out\":\"0x%h\",\"lp_ctrl_ack\":%0d,\"lp_data_ack\":%0d,\"ctrlupd_ack\":%0d,\"phyupd_req\":%0d,\"phymstr_req\":%0d,\"init_complete\":%0d,\"error\":%0d,\"train_ack\":%0d,\"train_token\":%0d,\"prepared\":%0d,\"prepare_error\":%0d,\"prepared_epoch\":%0d,\"committed_epoch\":%0d,\"mailbox\":\"0x%h\",\"config_prepare\":%0d,\"config_commit\":%0d,\"profile_image\":\"0x%h\",\"request_valid\":%0d,\"request_ready\":%0d,\"completion_valid\":%0d,\"completion_ready\":%0d,\"inline_admit\":%0d,\"inline_physical\":%0d,\"inline_result\":%0d,\"inline_complete\":%0d}",
    c2_tick,c2_case,cycles,c2_timestamp_ps(),memory_profile.ratio==2 ? 1 : 0,rst_n,
    memory_reset,dut.destructive_reset,reset_confirmed,host_settled,status_value.state==2,1'b0,
    status_value.fault,status_value.memory_trusted,status_value.owner,status_value.boot_epoch,status_value.config_epoch,status_value.config_valid,
    status_value.train_done,management_in.training_epoch,status_value.drained,status_value.pin_drained,status_value.ecc_drained,dut.inline_drained,
    dut.inline_ecc.data_committed_q,command_valid,command_ready,command,command_rejected,management_out.ctrlupd_req,
    management_out.phyupd_ack,management_out.init_start,dut.fsp_busy,fsp_ready,management_out.lp_ctrl_req || management_out.lp_data_req,management_out.phymstr_ack,management_out.refresh_request,
    phy_refresh_done,report_valid,report_sr,dut.temperature,memory_profile.ratio,ca.cs,
    ca.address,wck,write_value,read_value,return_value,mr4,mr43,management_in,
    management_out,management_in.lp_ctrl_ack,management_in.lp_data_ack,management_in.ctrlupd_ack,management_in.phyupd_req,management_in.phymstr_req,management_in.init_complete,management_in.error,
    train_ack,train_token,prepared,prepare_error,prepared_epoch,committed_epoch,
    config_mailbox,config_mailbox.prepare,config_mailbox.commit,memory_profile,request_valid,request_ready,completion_valid,completion_ready,dut.inline_ecc.request_valid_i && dut.inline_ready,
    dut.inline_physical_valid && dut.inline_physical_ready,dut.inline_ecc.result_valid_i && dut.inline_result_ready,dut.inline_completion_valid && completion_ready);
    c2_tick++;
  end
endtask
// Handshakes belong to the later clock edge, after return combinational settling.
always @(posedge clk) begin
  if(c2_stream!=0) begin
    $fdisplay(c2_stream,"{\"kind\":\"handshakes\",\"time_ps\":%0d,\"tick\":%0d,\"case_id\":%0d,\"offer\":%0d,\"request\":%0d,\"admit\":%0d,\"physical\":%0d,\"result\":%0d,\"complete\":%0d,\"original\":%0d}",c2_timestamp_ps(),c2_tick-1,c2_case,dut.active.inline_ecc && !dut.destructive_reset && completion_valid,!dut.destructive_reset && request_valid && request_ready,!dut.destructive_reset && dut.inline_ecc.request_valid_i && dut.inline_ready,!dut.destructive_reset && dut.inline_physical_valid && dut.inline_physical_ready,!dut.destructive_reset && dut.inline_ecc.result_valid_i && dut.inline_result_ready,!dut.destructive_reset && dut.inline_completion_valid && completion_ready,!dut.destructive_reset && completion_valid && completion_ready);
    if(dut.active.inline_ecc && !dut.destructive_reset) begin
      if(completion_valid) c2_result("original_offer",completion);
      if(request_valid && request_ready) c2_granule("original_admit",request,request_boot,request_epoch);
      if(dut.inline_ecc.request_valid_i && dut.inline_ready) c2_granule("inline_admit",request,status_value.boot_epoch,status_value.config_epoch);
      if(dut.inline_physical_valid && dut.inline_physical_ready) c2_granule("inline_physical",dut.inline_physical,status_value.boot_epoch,status_value.config_epoch);
      if(dut.inline_ecc.result_valid_i && dut.inline_result_ready) c2_result("inline_result",dut.backend_completion);
      if(dut.inline_completion_valid && completion_ready) c2_result("inline_complete",dut.inline_completion);
      if(completion_valid && completion_ready) c2_result("original_complete",completion);
    end
  end
end
task automatic c2_granule(input string kind,input lpddr5_mc_granule_t g,input logic [31:0] boot_value,config_value);
  $fdisplay(c2_stream,"{\"kind\":\"%s\",\"tick\":%0d,\"case_id\":%0d,\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"rank\":%0d,\"row\":%0d,\"bank\":%0d,\"column\":%0d,\"write\":%0d,\"bytes\":%0d,\"mask\":\"0x%h\",\"data\":\"0x%h\",\"payload\":\"0x%h\"}",
    kind,c2_tick-1,c2_case,g.tag,g.generation,boot_value,config_value,g.rank_index,g.row,{g.bank_group,g.bank},g.column,g.is_write,g.is_64_bytes ? 64 : 32,g.byte_enable,g.data,g);
endtask
task automatic c2_result(input string kind,input lpddr5_mc_channel_completion_t v);
  $fdisplay(c2_stream,"{\"kind\":\"%s\",\"tick\":%0d,\"case_id\":%0d,\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"write\":%0d,\"physical\":%0d,\"integrity\":%0d,\"failed\":%0d,\"response\":%0d,\"data\":\"0x%h\",\"payload\":\"0x%h\"}",
    kind,c2_tick-1,c2_case,v.identity.tag,v.identity.generation,v.identity.boot_epoch,v.identity.config_epoch,v.is_write,v.physical_done,v.integrity_done,v.failed,v.response,v.data,v);
endtask
final begin
  if(c2_stream!=0) begin
    $fdisplay(c2_stream,"{\"kind\":\"end\",\"tick\":%0d,\"complete\":%0d}",c2_tick,c2_complete);
    $fclose(c2_stream);
  end
end
