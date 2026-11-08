// Observe actual scheduler boundaries while AXI/CDC/data and maintenance run.
// All addresses are disjoint; there are no missing-W, cap or ECC exclusions
// after scheduler admission. Row/timing/WCK costs stay inside the measured G.
for(genvar f=0;f<CHANNELS;f++) begin : fairness_observers
  initial begin
    wait(rst_n);
    forever begin
      @(posedge clk);
      if(fairness_stream!=0) begin
    lpddr5_mc_granule_t request_value,issue_value;
    string admitted,issued;
    bit pair_live,maintenance_pause,owner_pause,response_pause;
    int pause_reason;
    check(dut.channels[f].channel.backend.scheduler.ck_q==64'(cap_ck),"independent fairness CK origin");
    request_value=dut.channels[f].channel.backend.scheduler.request_i;
    issue_value=dut.channels[f].channel.backend.scheduler.launch_o;
    admitted="null";issued="null";
    if(dut.channels[f].channel.backend.scheduler.request_valid_i
        && dut.channels[f].channel.backend.scheduler.request_ready_o)
      admitted=$sformatf("{\"tag\":%0d,\"generation\":%0d,\"write\":%0d,\"port\":%0d,\"qos\":%0d,\"bytes\":%0d,\"address\":%0d}",
        request_value.tag,request_value.generation,request_value.is_write,request_value.port_index,request_value.qos_class,
        request_value.is_64_bytes ? 64 : 32,request_value.address);
    if(dut.channels[f].channel.backend.scheduler.launch_valid_o)
      issued=$sformatf("{\"tag\":%0d,\"generation\":%0d,\"write\":%0d,\"port\":%0d,\"qos\":%0d,\"bytes\":%0d,\"address\":%0d}",
        issue_value.tag,issue_value.generation,issue_value.is_write,issue_value.port_index,issue_value.qos_class,
        issue_value.is_64_bytes ? 64 : 32,issue_value.address);
    if(admitted!="null" || issued!="null") begin
      check(dut.channels[f].channel.backend.scheduler.config_i.read_urgent_age==16'(fairness_age)
        && dut.channels[f].channel.backend.scheduler.config_i.write_urgent_age==16'(fairness_age)
        && dut.channels[f].channel.backend.scheduler.config_i.age_tick_div==8,"fairness active age configuration");
    end
    pair_live=dut.channels[f].channel.backend.scheduler.pair_pending_o;
    maintenance_pause=dut.channels[f].channel.backend.register_pending_i
      || dut.channels[f].channel.backend.register_owner_q
      || dut.channels[f].channel.backend.maintenance_valid_i || dut.channels[f].channel.backend.close_i;
    owner_pause=!dut.channels[f].channel.backend.issue_i || !dut.channels[f].channel.backend.service_i;
    response_pause=!dut.channels[f].channel.backend.scheduler.launch_ready_i;
    pause_reason=pair_live ? 0 : maintenance_pause ? 3 : owner_pause ? 2 : response_pause ? 1 : 0;
    $fdisplay(fairness_stream,"{\"kind\":\"cycle\",\"ck\":%0d,\"channel\":%0d,\"admit\":%s,\"issue\":%s,\"pair\":%0d,\"maintenance\":%0d,\"ownership\":%0d,\"response\":%0d,\"pause\":%0d}",
      cap_ck,f,admitted,issued,pair_live,maintenance_pause,owner_pause,response_pause,pause_reason);
      end
    end
  end
end

task automatic fairness_source(input int p);
  for(int n=0;n<128;n++) begin
    longint unsigned at;
    logic [15:0] source_id;
    // Limit each source to 32 live addresses, including both directions.
    // Thus each channel has at most 64 pending candidates for the D64 bound.
    while((fairness_mode==2 ? 2*n+2 : n+1)-b_count[p]-r_count[p]/(64/BYTES)>32) source_negative(p);
    at=64'd131072+64'(int'(n*256+p*128+((n/4+p)%2)*64));
    // Four rows in one bank per source, with distinct columns/addresses.
    // Reads use a separate four-row set within the programmed 1-MiB region.
    // Slow row turnover creates queue pressure without forced internal stalls.
    if(fairness_rows!=0) at=64'd131072+64'(int'((n%4)*65536+(n/4)*128+p*8192+((n/4+p)%2)*64));
    // Keep QoS independent of row in the churn case. A class per 32-request
    // block lets later high-QoS work compete with older urgent low-QoS work.
    source_id=fairness_rows!=0 ? 16'((n%32)*4+n/32) : 16'(n%64);
    if(fairness_mode!=0) begin send_aw(p,16'd64+source_id,at,64/BYTES);send_w(p,at,64/BYTES);end
    if(fairness_mode!=1) send_ar(p,16'd96+source_id,at+(fairness_rows!=0 ? 64'd524288 : 64'd65536),64/BYTES);
  end
endtask

task automatic fairness_case;
  bit complete,pins_idle;
  check(PORTS==2 && CHANNELS==2 && RANKS==1 && DATA_BITS==128 && DEPTH==64
    && PROFILE_NUMBER==4 && profile.always_on,"fairness reference workload shape");
  for(int p=0;p<PORTS;p++) begin
    source_negative(p);bready[p]=fairness_mode!=1;rready[p]=fairness_mode==2;
  end
  fork
    fairness_source(0);
    fairness_source(1);
    begin
      // Hold a real source response while the producer runs. The trace
      // records whether physical response capacity becomes unavailable;
      // no internal completion/ready is forced.
      if(fairness_mode==0) wait(|rvalid);
      else if(fairness_mode==1) wait(|bvalid);
      if(fairness_mode!=2) repeat(600) @(negedge clk);
      for(int p=0;p<PORTS;p++) begin source_negative(p);bready[p]=1;rready[p]=1;end
    end
  join
  complete=0;
  for(int n=0;n<12000;n++) begin
    @(negedge clk);complete=1;
    for(int p=0;p<PORTS;p++) begin
      if(fairness_mode!=0) complete&=b_count[p]==128;
      if(fairness_mode!=1) complete&=r_count[p]==128*(64/BYTES);
    end
    if(complete) break;
  end
  check(complete && !host_fault,"fairness complete source settlement");wait_settled();
  enable=0;send_command(CH_CMD_QUIESCE);pins_idle=0;
  for(int n=0;n<3000;n++) begin
    @(negedge clk);pins_idle=1;
    for(int c=0;c<CHANNELS;c++) pins_idle&=status_value[c].pin_drained && status_value[c].drained;
    if(pins_idle) break;
  end
  check(pins_idle,"fairness physical/maintenance tail");
  @(negedge clk);finish_observations=1;
  $fdisplay(fairness_stream,"{\"kind\":\"end\",\"ck\":%0d}",cap_ck);$fclose(fairness_stream);fairness_stream=0;
  repeat(2) @(negedge clk);
  $display("DATAPATH_FAIRNESS_PASS mode=%0d divisor=%0d checks=%0d",fairness_mode,SERVICE_DIV,checks);
  $display("DATAPATH_INTEGRATION_PASS checks=%0d",checks);$finish;
endtask
