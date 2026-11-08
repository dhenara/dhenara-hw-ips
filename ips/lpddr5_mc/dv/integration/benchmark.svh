// Included by controller_tb. Sources never insert a beat bubble when READY.
// Disjoint per-port lease buckets avoid an artificial ownership handover.
logic benchmark_started=0,benchmark_sources=0;
int benchmark_direction=0,benchmark_service=2,benchmark_periods=100,benchmark_smoke=0;
int benchmark_admitted[PORTS],benchmark_completed[PORTS],benchmark_wbeats[PORTS];
int benchmark_read_beat[PORTS];
logic [PORTS-1:0] benchmark_address_done=0,benchmark_data_done=0;
longint benchmark_start_ps;
function automatic int benchmark_address(input int port_value,sequence_value);
  int channel_value,bank_value,column_value,row_value;
  channel_value=sequence_value%2;bank_value=(sequence_value/2)%16;
  column_value=(sequence_value/32)%16;row_value=sequence_value/512;
  return (row_value<<16)|(bank_value<<12)|(port_value<<11)|(column_value<<7)|(channel_value<<6);
endfunction
for(genvar bp=0;bp<PORTS;bp++) begin : benchmark_ports
  initial begin : addresses
    int seq;
    bit offered;
    seq=0;offered=0;
    if(BENCHMARK) begin
      wait(benchmark_started);
      forever begin
        source_negative(bp);
        if(!offered) begin
          if(!benchmark_sources) break;
          check(seq<65536,"benchmark identity exhaustion");
          if(benchmark_direction==1) aw[bp]=address(16'(seq),benchmark_address(bp,seq),4);
          else ar[bp]=address(16'(seq),benchmark_address(bp,seq),4);
          offered=1;
        end
        if(benchmark_direction==1) awvalid[bp]=1;else arvalid[bp]=1;
        source_positive(bp);
        if(benchmark_direction==1 ? awready[bp] : arready[bp]) begin seq++;offered=0;end
      end
      awvalid[bp]=0;arvalid[bp]=0;benchmark_address_done[bp]=1;
    end
  end
  initial begin : data_source
    int seq,beat,at;
    seq=0;beat=0;
    if(BENCHMARK) begin
      wait(benchmark_started);
      if(benchmark_direction==1) begin
        // Populate the declared source address credits before starting W.
        // Otherwise AW/W startup can leave the fixed ten-period warm-up with
        // a partly filled source queue even though later service is saturated.
        // This remains inside the recorded warm-up: no window is shifted and
        // responses stay ready from BENCHMARK_START through final settlement.
        wait(benchmark_admitted[bp]==DEPTH);
        $display("BENCHMARK_WRITE_PREFILL port=%0d admitted=%0d ps=%0d",bp,benchmark_admitted[bp],longint'($realtime*1000.0));
        forever begin
          source_negative(bp);
          if(benchmark_address_done[bp] && seq==benchmark_admitted[bp]) break;
          wvalid[bp]=seq<benchmark_admitted[bp];
          wd[bp]='0;wd[bp].last=beat==3;wd[bp].strobe[15:0]='1;
          at=benchmark_address(bp,seq)+beat*16;
          for(int j=0;j<16;j++) wd[bp].data[j*8+:8]=8'((at+j)^((at+j)>>8)^90);
          source_positive(bp);
          if(wvalid[bp] && wready[bp]) begin
            beat++;if(beat==4) begin beat=0;seq++;end
          end
        end
        wvalid[bp]=0;
      end
      benchmark_data_done[bp]=1;
    end
  end
  initial forever begin : response_check
    int at;
    @(posedge axi_clk[bp]);
    if(BENCHMARK && benchmark_started) begin
      if((awvalid[bp] && awready[bp]) || (arvalid[bp] && arready[bp])) benchmark_admitted[bp]++;
      if(wvalid[bp] && wready[bp]) benchmark_wbeats[bp]++;
      if(bvalid[bp] && bready[bp]) begin
        check(benchmark_direction==1 && b[bp].id==16'(benchmark_completed[bp]) && b[bp].response==0,"benchmark B identity/status");
        check(benchmark_wbeats[bp]>=4*(benchmark_completed[bp]+1),"benchmark B before complete W");
        benchmark_completed[bp]++;
      end
      if(rvalid[bp] && rready[bp]) begin
        check(benchmark_direction==0 && r[bp].id==16'(benchmark_completed[bp]) && r[bp].response==0
          && r[bp].last==(benchmark_read_beat[bp]==3),"benchmark R identity/status/last");
        at=benchmark_address(bp,benchmark_completed[bp])+16*benchmark_read_beat[bp];
        for(int j=0;j<16;j++) check(r[bp].data[j*8+:8]==8'((at+j)^((at+j)>>8)^90),"benchmark returned payload");
        benchmark_read_beat[bp]++;
        if(benchmark_read_beat[bp]==4) begin benchmark_read_beat[bp]=0;benchmark_completed[bp]++;end
      end
    end
  end
end
task automatic benchmark_run;
  if($value$plusargs("benchmark_smoke=%d",benchmark_smoke) && benchmark_smoke!=0) benchmark_periods=2;
  check(PORTS==2 && CHANNELS==2 && RANKS==1 && DATA_BITS==128 && DEPTH==64
    && PROFILE_NUMBER==4,"exact benchmark configuration");
  check(benchmark_direction inside {0,1} && benchmark_service inside {1,2,4} && (benchmark_periods>=100 || benchmark_smoke==1),"benchmark runtime settings");
  for(int p=0;p<PORTS;p++) begin
    benchmark_admitted[p]=0;benchmark_completed[p]=0;benchmark_wbeats[p]=0;benchmark_read_beat[p]=0;
  end
  @(negedge clk);
  benchmark_start_ps=longint'($realtime*1000.0);
  benchmark_started=1;benchmark_sources=1;bready='1;rready='1;
  $display("BENCHMARK_START ps=%0d direction=%0d service=%0d periods=%0d",benchmark_start_ps,benchmark_direction,benchmark_service,benchmark_periods);
  for(int period_value=0;period_value<benchmark_periods;period_value++) begin
    repeat(3000) @(negedge clk);
    #0.001;
    check(!host_fault && enable,"benchmark lost controller service");
    $display("BENCHMARK_PERIOD period=%0d admitted0=%0d admitted1=%0d completed0=%0d completed1=%0d backlog0=%0d backlog1=%0d",
      period_value+1,benchmark_admitted[0],benchmark_admitted[1],benchmark_completed[0],benchmark_completed[1],
      benchmark_admitted[0]-benchmark_completed[0],benchmark_admitted[1]-benchmark_completed[1]);
    $fflush();
  end
  benchmark_sources=0;
  wait((&benchmark_address_done) && (&benchmark_data_done));
  wait_settled();
  for(int p=0;p<PORTS;p++) check(benchmark_admitted[p]==benchmark_completed[p],"benchmark accepted tail lost");
  write_csr(INDEX_CONTROL,2);
  for(int c=0;c<CHANNELS;c++) wait(status_value[c].state==1 && status_value[c].drained);
  @(negedge clk);finish_observations=1;repeat(2) @(negedge clk);
  $display("CONTROLLER_INTEGRATION_PASS benchmark checks=%0d",checks);$finish;
endtask
// Diagnostic snapshots explain the actual CK engine's unused opportunities.
// They never substitute for external CA/data/response checks.
for(genvar bc=0;bc<CHANNELS;bc++) begin : benchmark_engine_observers
  int observed_ck=0;
  logic [2*DEPTH-1:0] queued;
  logic [14:0] flags;
  initial forever begin
    @(posedge clk);
    if(BENCHMARK && observe_traffic && observer.stream!=0) begin
      queued='0;
      for(int i=0;i<2*DEPTH;i++) queued[i]=dut.datapath.channels[bc].channel.backend.scheduler.entries_q[i].valid;
      flags={dut.datapath.channels[bc].channel.register_zq_q,
        dut.datapath.channels[bc].channel.host_admission,
        dut.datapath.channels[bc].channel.wck_disabled,
        dut.datapath.channels[bc].channel.close_pages,
        dut.datapath.channels[bc].channel.register_valid,
        dut.datapath.channels[bc].channel.maintenance_valid,
        dut.datapath.channels[bc].channel.backend.register_owner_q,
        dut.datapath.channels[bc].channel.backend.block_issue,
        dut.datapath.channels[bc].channel.backend.pair_pending,
        dut.datapath.channels[bc].channel.backend.scheduler.legal,
        dut.datapath.channels[bc].channel.backend.scheduler.selected,
        dut.datapath.channels[bc].channel.backend.launch_valid,
        dut.datapath.channels[bc].channel.backend.launch_ready,
        dut.datapath.channels[bc].channel.backend.scheduler.command_enable_i,
        dut.datapath.channels[bc].channel.backend.scheduler.issue_enable_i};
      $fdisplay(observer.stream,"{\"kind\":\"engine\",\"ps\":%0d,\"ck\":%0d,\"channel\":%0d,\"queue\":\"0x%h\",\"flags\":%0d,\"operation\":%0d}",
        observer.timestamp_ps(),observed_ck,bc,queued,flags,dut.datapath.channels[bc].channel.backend.scheduler.selected_operation);
      observed_ck++;
    end
  end
end
