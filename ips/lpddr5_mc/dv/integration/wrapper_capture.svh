// Wrapper-only capture lifecycle. Callbacks read live payloads before NBA;
// neither this skeleton nor the stop task reconstructs an omitted witness.
bit wm_axi_capture=1,wm_sample_capture=1,wm_capture_closed=0;
bit wm_host_seen=0;
int wm_host_edge_tick=0;
function automatic bit wm_accept_sample();
  return wm_stream!=0 && wm_sample_capture;
endfunction
for(genvar p=0;p<PORTS;p++) begin : wm_address_capture
  initial forever begin
    @(posedge axi_clk[p]);
    if(wm_stream!=0 && wm_axi_capture && observe_traffic && monitor_rst_n[p]) wm_capture_address(p);
  end
end
for(genvar c=0;c<CHANNELS;c++) begin : wm_channel_capture
  always @(posedge clk) if(wm_stream!=0 && wm_tick[c]>wm_edge_tick[c]) begin
    wm_edge_tick[c]<=wm_tick[c];
    wm_observers[c].capture_edge();
  end
end
initial forever begin
  @(posedge clk);
  if(wm_stream!=0 && wm_tick[0]>wm_host_edge_tick) begin
    wm_host_seen=1;wm_host_edge_tick=wm_tick[0];
    wm_capture_host();
  end
end
// Reset remains observable throughout pending-edge drain, including an NBA
// reset at the last rising CK. It is not masked by finish_observations.
always @(negedge wm_core_reset_n) if(wm_stream!=0 && wm_host_seen) wm_capture_reset();
task automatic wrapper_stop_observations;
  assert(!wm_capture_closed) else $fatal(1,"duplicate wrapper stop");
  // Include every source event at the request timestamp, independent of
  // active-region order. Source and address observers share this same gate.
  #0.001;wm_axi_capture=0;
  // Two further falling samples retain real rising host/counter observations
  // after the final source edge or first pending core handshake. The fixture
  // has already checked all-owner settlement; offline finish still rejects
  // an unfinished owner or a missing next-edge counter update.
  repeat(2) @(negedge clk);
  #0.001;wm_sample_capture=0;finish_observations=1;
  // Drain the last sampled tick in every observer before closing the wrapper.
  // The unchanged source observer also captures this rising CK then closes.
  @(posedge clk);#0.001;
  for(int c=0;c<CHANNELS;c++)
    assert(wm_edge_tick[c]==wm_tick[c]) else $fatal(1,"pending wrapper channel tail");
  assert(wm_host_edge_tick==wm_tick[0]) else $fatal(1,"pending wrapper host tail");
  wm_close_capture();wm_capture_closed=1;
endtask
