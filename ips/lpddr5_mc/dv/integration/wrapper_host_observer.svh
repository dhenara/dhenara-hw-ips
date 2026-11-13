// Atomic host seam and pending correlation ledger, before the core NBA.
// The next host_edge observes this edge's counter update; reset has its own
// falling-edge witness. No DUT state or production diagnostic is assigned.
`define WM_CORE dut.datapath.host.core
bit wm_host_seen=0;
int wm_host_edge_tick=0;
task automatic wm_host_request(input lpddr5_mc_granule_t q,input logic [31:0] boot,epoch);
  $fwrite(wm_stream,"{\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"write\":%0d,\"channel\":%0d,\"port\":%0d,\"axi_id\":%0d,\"address\":\"0x%h\",\"bytes\":%0d,\"mask\":\"0x%h\",\"data\":\"0x%h\",\"bits\":\"0x%h\"}",q.tag,q.generation,boot,epoch,q.is_write,q.channel_index,q.port_index,q.axi_id,q.address,q.is_64_bytes ? 64 : 32,q.byte_enable,q.data,q);
endtask
task automatic wm_host_completion(input lpddr5_mc_host_pkg::lpddr5_mc_host_completion_t v,input int selected);
  $fwrite(wm_stream,"{\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"write\":%0d,\"physical\":%0d,\"integrity\":%0d,\"failed\":%0d,\"response\":%0d,\"data\":\"0x%h\",\"bits\":\"0x%h\",\"match_slot\":%0d}",v.identity.tag,v.identity.generation,v.identity.boot_epoch,v.identity.config_epoch,v.is_write,v.physical_done,v.integrity_done,v.failed,v.response,v.data,v,selected);
endtask
task automatic wm_host_channel(input int c,input bit inline_mode,write_link,posted);
  $fwrite(wm_stream,"{\"channel\":%0d,\"request_valid\":%0d,\"request_ready\":%0d,\"completion_valid\":%0d,\"completion_ready\":%0d,\"inline\":%0d,\"write_link\":%0d,\"posted\":%0d,\"request\":",
    c,dut.datapath.request_valid[c],dut.datapath.request_ready[c],dut.datapath.completion_valid[c],dut.datapath.completion_ready[c],inline_mode,write_link,posted);
  wm_host_request(dut.datapath.requests[c],dut.datapath.request_boot[c],dut.datapath.request_epoch[c]);
  $fwrite(wm_stream,",\"completion\":");
  wm_host_completion(dut.datapath.host_completions[c],$signed(`WM_CORE.completion_select[c]));
  $fwrite(wm_stream,"}");
endtask
always @(negedge dut.datapath.host.core_reset_n) if(wm_stream!=0 && wm_host_seen && !finish_observations)
  $fdisplay(wm_stream,"{\"kind\":\"host_reset\",\"ps\":%0d,\"tick\":%0d,\"reset_n\":0,\"count\":%0d}",
    observer.timestamp_ps(),wm_host_edge_tick-1,`WM_CORE.stale_completions_o);
// A testbench sampling coroutine reads the asynchronous reset as data; it
// neither clocks that reset through logic nor introduces a reset synchronizer.
initial forever begin : wm_host_edge
  bit separator;
  @(posedge clk);
  if(wm_stream!=0 && wm_tick[0]>wm_host_edge_tick && !finish_observations) begin
  wm_host_seen=1;wm_host_edge_tick=wm_tick[0];
  $fwrite(wm_stream,"{\"kind\":\"host_edge\",\"ps\":%0d,\"tick\":%0d,\"reset_n\":%0d,\"fatal\":%0d,\"fault\":%0d,\"recover\":%0d,\"enable\":%0d,\"settled\":%0d,\"config_valid\":%0d,\"cfg_valid\":%0d,\"cfg_ready\":%0d,\"config_boot\":%0d,\"config_epoch\":%0d,\"cfg_boot\":%0d,\"cfg_epoch\":%0d,\"count\":%0d,\"external_abort\":%0d,\"posted_early\":%0d,\"ledger\":[",
    observer.timestamp_ps(),wm_tick[0]-1,`WM_CORE.rst_ni,`WM_CORE.fatal_i,`WM_CORE.fault_o,`WM_CORE.recover_i,`WM_CORE.enable_i,`WM_CORE.host_settled_o,
    `WM_CORE.config_valid_o,`WM_CORE.cfg_valid_i,`WM_CORE.cfg_ready_o,`WM_CORE.config_q.boot_epoch,`WM_CORE.config_q.config_epoch,`WM_CORE.cfg_i.boot_epoch,`WM_CORE.cfg_i.config_epoch,
    `WM_CORE.stale_completions_o,int'(`WM_CORE.EXTERNAL_ABORT_LEDGER),int'(`WM_CORE.POSTED_EARLY));
  separator=0;
  for(int s=0;s<2*PORTS*DEPTH;s++) if(`WM_CORE.transactions[s].valid && `WM_CORE.transactions[s].pending) begin
    if(separator) $fwrite(wm_stream,",");separator=1;
  $fwrite(wm_stream,"{\"slot\":%0d,\"valid\":%0d,\"pending\":%0d,\"channel\":%0d,\"write\":%0d,\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"physical\":%0d,\"integrity\":%0d,\"port\":%0d,\"source_slot\":%0d,\"source_generation\":%0d,\"axi_id\":%0d,\"merged\":%0d,\"follower\":%0d,\"first\":%0d,\"beats\":%0d,\"length\":%0d}",s,`WM_CORE.transactions[s].valid,`WM_CORE.transactions[s].pending,`WM_CORE.transactions[s].channel_index,`WM_CORE.transactions[s].write_access,`WM_CORE.transactions[s].identity.tag,`WM_CORE.transactions[s].identity.generation,`WM_CORE.transactions[s].identity.boot_epoch,`WM_CORE.transactions[s].identity.config_epoch,`WM_CORE.transactions[s].physical_done,`WM_CORE.transactions[s].chunk_integrity_done,`WM_CORE.transactions[s].port_index,`WM_CORE.transactions[s].source_slot,`WM_CORE.transactions[s].generation,`WM_CORE.transactions[s].address.id,`WM_CORE.transactions[s].merged,`WM_CORE.transactions[s].merge_follower,`WM_CORE.transactions[s].chunk_first,`WM_CORE.transactions[s].chunk_beats,`WM_CORE.transactions[s].address.len);
  end
  $fwrite(wm_stream,"],\"channels\":[");
  wm_host_channel(0,dut.datapath.channels[0].channel.active.inline_ecc,dut.datapath.channels[0].channel.active.profile.write_link,dut.datapath.channels[0].channel.active.posted);
  $fwrite(wm_stream,",");
  wm_host_channel(1,dut.datapath.channels[1].channel.active.inline_ecc,dut.datapath.channels[1].channel.active.profile.write_link,dut.datapath.channels[1].channel.active.posted);
  $fdisplay(wm_stream,"]}");
  end
end
`undef WM_CORE
