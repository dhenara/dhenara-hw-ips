// Included by datapath_tb. Disabled requests stay queued across four complete
// cap windows, then an explicit external fault settles their AXI obligations.
// No reset or memory-preservation claim is part of this workload.
task automatic zero_cap_case;
  bit all_admitted,pins_idle;
  longint fault_time;
  check(PORTS==2 && CHANNELS==2 && INTEGRITY_MODE==0,"zero cap workload shape");
  for(int n=0;n<8;n++) for(int p=0;p<PORTS;p++) begin
    longint unsigned at;
    at=64'd32768+64'(int'((n*2+p)*128+((n/4+p)%2)*64));
    send_aw(p,16'(64+n),at,64/BYTES);send_w(p,at,64/BYTES);
    send_ar(p,16'(96+n),at+64'd32768,64/BYTES);
  end
  // Complete the enabled direction while the other direction is still live.
  if(zero_cap_direction==0) begin
    for(int p=0;p<PORTS;p++) wait_b(p,71,8);
  end else for(int n=0;n<8;n++) for(int p=0;p<PORTS;p++) begin
    longint unsigned at;
    at=64'd65536+64'(int'((n*2+p)*128+((n/4+p)%2)*64));
    read_result(p,16'(96+n),at,64/BYTES);
  end
  all_admitted=0;
  for(int n=0;n<1000;n++) begin
    @(negedge clk);all_admitted=1;
    for(int c=0;c<CHANNELS;c++) all_admitted&=zero_admissions[c]==8;
    if(all_admitted) break;
  end
  check(all_admitted,"zero-cap requests must reach the actual scheduler");
  $fdisplay(cap_stream,"{\"kind\":\"hold\",\"ck\":%0d}",cap_ck);
  repeat(2048) begin
    @(negedge clk);
    check(!host_fault,"zero-cap wait must retain healthy service");
    if(zero_cap_direction==0) check(rvalid==0,"disabled reads offered a response");
    else for(int p=0;p<PORTS;p++) check(b_count[p]==0 && !bvalid[p],"disabled writes offered a response");
  end
  // Stop ordinary issue and let real pin/maintenance owners drain. Logical
  // zero-cap requests remain live, so full host/channel settlement is false.
  enable=0;send_command(CH_CMD_QUIESCE);pins_idle=0;
  for(int n=0;n<3000;n++) begin
    @(negedge clk);pins_idle=1;
    for(int c=0;c<CHANNELS;c++) pins_idle&=status_value[c].pin_drained;
    if(pins_idle) break;
  end
  check(pins_idle && !settled,"zero-cap fault must follow pin drain with queued obligations");
  fatal=1;fault_time=observer.timestamp_ps();
  $display("ZERO_CAP_FAULT ps=%0d ck=%0d",fault_time,cap_ck);
  $fdisplay(observer.stream,"{\"kind\":\"fault\",\"ps\":%0d}",fault_time);
  $fdisplay(cap_stream,"{\"kind\":\"fault\",\"ck\":%0d,\"ps\":%0d}",cap_ck,fault_time);
  if(zero_cap_direction==0) begin
    for(int n=0;n<8;n++) for(int p=0;p<PORTS;p++) begin
      longint unsigned at;
      at=64'd65536+64'(int'((n*2+p)*128+((n/4+p)%2)*64));
      read_result(p,16'(96+n),at,64/BYTES,2);
    end
  end else for(int p=0;p<PORTS;p++) wait_b(p,71,8,2);
  wait_settled();check(host_fault,"explicit fault must remain latched after zero-cap settlement");
  @(negedge clk);finish_observations=1;
  $fdisplay(cap_stream,"{\"kind\":\"end\",\"ck\":%0d}",cap_ck);$fclose(cap_stream);cap_stream=0;
  repeat(2) @(negedge clk);
  $display("DATAPATH_ZERO_CAP_PASS mode=%0d direction=%0d checks=%0d",cap_mode,zero_cap_direction,checks);
  $display("DATAPATH_INTEGRATION_PASS checks=%0d",checks);$finish;
endtask
