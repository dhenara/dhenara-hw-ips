// Blocking assignments are intentional in this untimed scoreboard/driver.
/* verilator lint_off BLKSEQ */
/* verilator lint_off UNUSEDSIGNAL */
// Common-clock behavioral checks. The memory responder uses byte-address
// arithmetic independent of the production splitter and does not model DFI.
module lpddr5_mc_host_core_tb #(parameter int DATA_BITS=128, parameter int QUEUE_DEPTH=16, parameter bit EARLY_POSTED=0);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_host_pkg::*;
  localparam int PORTS=2, CHANNELS=2, DEPTH=QUEUE_DEPTH, BUS_BYTES=DATA_BITS/8;
  logic clk=0;
  always #5 clk=~clk;
  /* verilator lint_off SYNCASYNCNET */
  logic rst_n=0;
  /* verilator lint_on SYNCASYNCNET */
  logic enable=0, fatal=0, recover=0, cfg_valid=0, cfg_ready;
  lpddr5_mc_host_config_t cfg;
  logic config_valid, fault, settled, late_error;
  logic [31:0] stale_count;
  logic [PORTS-1:0] aw_valid=0, ar_valid=0, w_valid=0, aw_ready, ar_ready, w_ready;
  lpddr5_mc_host_address_t [PORTS-1:0] aw, ar;
  lpddr5_mc_host_write_t [PORTS-1:0] w;
  logic [PORTS-1:0] b_valid, r_valid, b_ready=0, r_ready=0;
  lpddr5_mc_axi_b_t [PORTS-1:0] b;
  lpddr5_mc_axi_read_t [PORTS-1:0] r;
  logic [PORTS-1:0][5:0] b_slot, r_slot;
  logic [PORTS-1:0][15:0] b_generation, r_generation;
  logic [PORTS-1:0] event_valid=0, event_ready;
  lpddr5_mc_host_response_event_t [PORTS-1:0] response_event;
  lpddr5_mc_host_response_event_t [PORTS-1:0] retire;
  logic [PORTS-1:0][1:0][DEPTH-1:0] core_available;
  logic [PORTS-1:0] retire_valid, retire_ready='1;
  logic [CHANNELS-1:0] request_valid, request_ready, completion_valid=0, completion_ready;
  lpddr5_mc_granule_t [CHANNELS-1:0] request;
  logic [CHANNELS-1:0][31:0] request_boot, request_epoch;
  lpddr5_mc_host_completion_t [CHANNELS-1:0] completion;
  logic [CHANNELS-1:0] feedback_valid, feedback_ready='1;
  lpddr5_mc_host_feedback_t [CHANNELS-1:0] feedback;
  logic [7:0] actual_memory[65536], expected_memory[65536];
  logic [31:0] source_sequence[PORTS];
  logic [15:0] generation[PORTS][DEPTH];
  int requests_seen=0, physical_completions=0, integrity_completions=0;
  int feedback_exposures=0, feedback_handshakes=0;
  int tests=0;
  logic auto_channel=1, fail_next=0, posted_delay=0, integrity_first=0, stale_first=0;
  logic manual_channel=0;
  lpddr5_mc_granule_t manual_granules[DEPTH+2];
  int manual_requests=0;
  lpddr5_mc_granule_t pending[CHANNELS];
  logic busy[CHANNELS];
  int delay_count[CHANNELS];
  logic sent_physical[CHANNELS], committed[CHANNELS], injected_stale[CHANNELS];
  logic [31:0] pending_boot[CHANNELS], pending_epoch[CHANNELS];

  lpddr5_mc_host_core #(.PORTS(PORTS),.CHANNELS(CHANNELS),.RANKS(1),
    .DATA_BITS(BUS_BYTES*8),.ID_BITS(8),.ADDRESS_BITS(40),.DEPTH(DEPTH),.POSTED_EARLY(EARLY_POSTED)) dut (
    .clk_i(clk),.rst_ni(rst_n),.enable_i(enable),.fatal_i(fatal),.recover_i(recover),
    .cfg_valid_i(cfg_valid),.cfg_ready_o(cfg_ready),.cfg_i(cfg),
    .config_valid_o(config_valid),.fault_o(fault),.host_settled_o(settled),.late_error_o(late_error),
    .stale_completions_o(stale_count),.aw_valid_i(aw_valid),.ar_valid_i(ar_valid),.w_valid_i(w_valid),
    .aw_ready_o(aw_ready),.ar_ready_o(ar_ready),.w_ready_o(w_ready),.aw_i(aw),.ar_i(ar),.w_i(w),
    .b_valid_o(b_valid),.r_valid_o(r_valid),.b_ready_i(b_ready),.r_ready_i(r_ready),.b_o(b),.r_o(r),
    .b_slot_o(b_slot),.r_slot_o(r_slot),.b_generation_o(b_generation),.r_generation_o(r_generation),
    .response_event_valid_i(event_valid),.response_event_ready_o(event_ready),.response_event_i(response_event),
    .core_slot_available_o(core_available),.retire_valid_o(retire_valid),.retire_ready_i(retire_ready),.retire_o(retire),
    .request_valid_o(request_valid),.request_ready_i(request_ready),.request_o(request),
    .request_boot_epoch_o(request_boot),.request_config_epoch_o(request_epoch),
    .completion_valid_i(completion_valid),.completion_ready_o(completion_ready),.completion_i(completion),
    .feedback_valid_o(feedback_valid),.feedback_ready_i(feedback_ready),.feedback_o(feedback)
  );

  for (genvar c=0;c<CHANNELS;c++) begin : channel_ready
    assign request_ready[c] = manual_channel || (auto_channel && !busy[c]);
  end
  // Requests reserve a single channel slot. Data becomes physical only after
  // a fixed delay; posted mode deliberately separates integrity settlement.
  always @(negedge clk) if(!manual_channel) begin
    for (int c=0;c<CHANNELS;c++) begin
      completion_valid[c]=0;
      if (rst_n && busy[c] && auto_channel) begin
        if (delay_count[c]>0) delay_count[c]--;
        else if(stale_first && !injected_stale[c]) begin
          completion[c]='0;
          completion[c].identity.tag=pending[c].tag;
          completion[c].identity.generation=pending[c].generation;
          completion[c].identity.boot_epoch=pending_boot[c]+1;
          completion[c].identity.config_epoch=pending_epoch[c];
          completion[c].is_write=pending[c].is_write;
          completion[c].physical_done=1;completion[c].integrity_done=1;
          completion_valid[c]=1;injected_stale[c]=1;delay_count[c]=3;
        end else begin
          completion[c]='0;
          completion[c].identity.tag=pending[c].tag;
          completion[c].identity.generation=pending[c].generation;
          completion[c].identity.boot_epoch=pending_boot[c];
          completion[c].identity.config_epoch=pending_epoch[c];
          completion[c].is_write=pending[c].is_write;
          completion[c].physical_done=!integrity_first || sent_physical[c];
          completion[c].integrity_done=!posted_delay || sent_physical[c];
          completion[c].failed=fail_next;
          completion[c].response=fail_next ? 2'b10 : 2'b00;
          for (int j=0;j<64;j++) begin
            int address;
            address=int'(pending[c].address)+j;
            if (address>=65536) $fatal(1,"memory model range");
            if (pending[c].is_write && pending[c].byte_enable[j] && completion[c].physical_done && !committed[c])
              actual_memory[address]=pending[c].data[j*8+:8];
            completion[c].data[j*8+:8]=actual_memory[address];
          end
          completion_valid[c]=1;
          if (completion[c].physical_done) committed[c]=1;
          if (completion[c].integrity_done) integrity_completions++;
          if (completion[c].integrity_done && completion[c].physical_done) begin
            busy[c]=0;
          end else begin
            sent_physical[c]=1;
            delay_count[c]=12;
          end
          if(completion[c].physical_done) physical_completions++;
        end
      end
    end
  end
  always @(posedge clk) begin
    if (rst_n) begin
      if(completion_ready!='1)$fatal(1,"completion backpressure unsupported by responder");
      for (int c=0;c<CHANNELS;c++) begin
        if (request_valid[c] && request_ready[c]) begin
          if(manual_channel)begin
            if(c!=0 || manual_requests>=DEPTH+2)$fatal(1,"manual channel overflow/routing");
            manual_granules[manual_requests]=request[c];manual_requests++;
          end else begin
            if (busy[c]) $fatal(1,"channel slot reused");
            pending[c]=request[c]; pending_boot[c]=request_boot[c]; pending_epoch[c]=request_epoch[c];
            delay_count[c]=3; busy[c]=1; sent_physical[c]=0; committed[c]=0; injected_stale[c]=0;
          end
          requests_seen++;
          if (request[c].channel_index!=1'(c)) $fatal(1,"channel routing field");
          if (request[c].address[6]!=1'(c)) $fatal(1,"channel striping");
          if (request[c].address[5:0] != 0) $fatal(1,"64-byte alignment");
          begin
            longint unsigned local_address;
            local_address=(64'(request[c].address)/128)*64+(64'(request[c].address)%64);
            if({request[c].bank_group,request[c].bank}!=4'(local_address/2048)
                ||request[c].row!=18'(local_address/32768)
                ||request[c].column!=12'((local_address%2048)/32))
              $fatal(1,"independent physical geometry mismatch");
          end
        end
        if (feedback_valid[c] && feedback_ready[c]) begin
          if (feedback[c].exposed) feedback_exposures++;
          if (feedback[c].handshaken) feedback_handshakes++;
        end
      end
    end
  end


  int trace_count=0;
  always @(posedge clk)begin
    if(rst_n && $test$plusargs("debug"))begin
      for(int p=0;p<PORTS;p++)begin
        if(aw_valid[p] && aw_ready[p])begin
          $display("TRACE AW p%0d source(slot%0d gen%0d seq%0d len%0d) dut(slot%0d gen%0d seq%0d len%0d index%0d expectedseq%0d)",p,
            aw[p].slot,aw[p].generation,aw[p].sequence_number,aw[p].axi.len,
            dut.aw_i[p].slot,dut.aw_i[p].generation,dut.aw_i[p].sequence_number,dut.aw_i[p].axi.len,dut.aw_slot[p],dut.expected_sequence_q[p]);
          #1;$display("TRACE TABLE valid0=%0b gen0=%0d len0=%0d valid1=%0b gen1=%0d len1=%0d",dut.transactions[0].valid,dut.transactions[0].generation,dut.transactions[0].address.len,dut.transactions[1].valid,dut.transactions[1].generation,dut.transactions[1].address.len);
        end
        if(w_valid[p] && w_ready[p] && trace_count<12)begin
          $display("TRACE W p%0d source(slot%0d gen%0d) dut(slot%0d gen%0d index%0d) table0valid%0b table1valid%0b",p,w[p].slot,w[p].generation,dut.w_i[p].slot,dut.w_i[p].generation,dut.w_slot[p],dut.transactions[0].valid,dut.transactions[1].valid);trace_count++;
        end
        if(event_valid[p])$display("TRACE EVENT p%0d slot%0d gen%0d write%0b exposure%0b ack%0b",p,response_event[p].slot,response_event[p].generation,response_event[p].write_response,response_event[p].exposed,response_event[p].handshaken);
      end
    end
  end

  task automatic tick(input int count=1);
    repeat(count) begin @(posedge clk); #1; end
  endtask
  function automatic longint unsigned oracle_at(input lpddr5_mc_axi_address_t a,input int beat);
    longint unsigned size_bytes, wrap_bytes, boundary, at;
    size_bytes=64'd1<<a.size;
    at=64'(a.address);
    if (a.burst==1 && beat!=0) at=(at/size_bytes)*size_bytes+64'(beat)*size_bytes;
    if (a.burst==2) begin
      wrap_bytes=size_bytes*(64'(a.len)+1);
      boundary=(64'(a.address)/wrap_bytes)*wrap_bytes;
      at=boundary+(64'(a.address)-boundary+64'(beat)*size_bytes)%wrap_bytes;
    end
    return at;
  endfunction
  function automatic logic [31:0] oracle_strobe(input lpddr5_mc_axi_address_t a,input int beat);
    longint unsigned at, limit;
    logic [31:0] mask;
    at=oracle_at(a,beat); limit=(at/(64'd1<<a.size)+1)*(64'd1<<a.size); mask=0;
    for(int j=0;j<BUS_BYTES;j++) if(at+64'(j)<limit) mask[5'((at+64'(j))%64'(BUS_BYTES))]=1;
    return mask;
  endfunction
  function automatic lpddr5_mc_axi_address_t make_address(
      input int address, beats, size, burst, axi_id=1);
    lpddr5_mc_axi_address_t value;
    value='0; value.address=40'(address); value.len=8'(beats-1);
    value.size=3'(size); value.burst=2'(burst); value.id=16'(axi_id); return value;
  endfunction
  task automatic admit(input int p, slot,input logic write_access,input lpddr5_mc_axi_address_t value);
    int timeout;
    lpddr5_mc_host_address_t address_payload;
    address_payload='0; address_payload.axi=value; address_payload.slot=6'(slot);
    address_payload.generation=generation[p][slot]; address_payload.sequence_number=source_sequence[p];
    @(negedge clk);
    if(write_access) begin
      aw[p]=address_payload; aw_valid[p]=1;
    end else begin
      ar[p]=address_payload; ar_valid[p]=1;
    end
    timeout=0;
    do begin
      @(posedge clk);
      timeout++; if(timeout>200) $fatal(1,"address timeout port %0d slot %0d",p,slot);
    end while (!(write_access?aw_ready[p]:ar_ready[p]));
    #1; source_sequence[p]++;
    @(negedge clk); aw_valid[p]=0; ar_valid[p]=0;
  endtask
  task automatic send_w(input int p,slot,input lpddr5_mc_axi_address_t value,
      input int malformed=-1,input bit update_expected=1);
    for(int beat=0;beat<=int'(value.len);beat++) begin
      int timeout;
      lpddr5_mc_host_write_t write_payload;
      write_payload='0;
      write_payload.slot=6'(slot); write_payload.generation=generation[p][slot];
      for(int lane=0;lane<BUS_BYTES;lane++) write_payload.axi.data[lane*8+:8]=8'(beat*17+lane*11+int'(value.id));
      write_payload.axi.strobe=oracle_strobe(value,beat);
      write_payload.axi.last=beat==int'(value.len);
      if(malformed==0 && beat==0) write_payload.axi.last=1;
      if(malformed==1 && beat==0) write_payload.axi.strobe=~write_payload.axi.strobe;
      if(malformed==2 && beat==int'(value.len))write_payload.axi.last=0;
      if(malformed==3)write_payload.axi.strobe &= 32'h55555555;
      @(negedge clk); w[p]=write_payload;
      w_valid[p]=1; timeout=0;
      do begin @(posedge clk); timeout++; if(timeout>1000)$fatal(1,"W timeout p%0d slot%0d beat%0d valid%0b gen%0d/%0d received%0d len%0d full%0b",p,slot,beat,dut.transactions[p*2*DEPTH+slot].valid,dut.transactions[p*2*DEPTH+slot].generation,w[p].generation,dut.transactions[p*2*DEPTH+slot].received,dut.transactions[p*2*DEPTH+slot].address.len,dut.transactions[p*2*DEPTH+slot].fully_buffered); end while(!w_ready[p]);
      if(update_expected) for(int lane=0;lane<BUS_BYTES;lane++) begin
        longint unsigned at, limit;
        at=oracle_at(value,beat); limit=(at/(64'd1<<value.size)+1)*(64'd1<<value.size);
        if(at+64'(lane)<limit && w[p].axi.strobe[5'((at+64'(lane))%64'(BUS_BYTES))])
          expected_memory[16'(at+64'(lane))]=w[p].axi.data[int'((at+64'(lane))%64'(BUS_BYTES))*8+:8];
      end
      #1; @(negedge clk); w_valid[p]=0;
    end
  endtask
  task automatic event_send(input int p,slot,input bit write_response,exposed,handshaken,input logic[1:0] response);
    @(negedge clk); response_event[p]='0;
    response_event[p].slot=6'(slot); response_event[p].generation=generation[p][slot];
    response_event[p].write_response=write_response; response_event[p].exposed=exposed;
    response_event[p].handshaken=handshaken; response_event[p].response=response; event_valid[p]=1;
    do begin @(posedge clk); end while(!event_ready[p]);
    #1; @(negedge clk); event_valid[p]=0;
  endtask
  task automatic receive_b(input int p,slot,input logic[1:0] expected_response,input int stall=0);
    int timeout;
    lpddr5_mc_axi_b_t saved;
    timeout=0;
    while(!b_valid[p]) begin tick(); timeout++; if(timeout>3000)$fatal(1,"B timeout port%0d slot%0d",p,slot); end
    saved=b[p];
    if(b_slot[p]!=6'(slot)||b_generation[p]!=generation[p][slot]||b[p].response!=expected_response)
      $fatal(1,"B mismatch p%0d slot%0d got%0d expected%0d",p,slot,b[p].response,expected_response);
    repeat(stall) begin tick(); if(!b_valid[p]||b[p]!==saved)$fatal(1,"stalled B changed"); end
    @(negedge clk); b_ready[p]=1;
    tick(); @(negedge clk); b_ready[p]=0;
    event_send(p,slot,1,1,1,expected_response);
    generation[p][slot]++;
  endtask
  task automatic receive_r(input int p,slot,input lpddr5_mc_axi_address_t value,
      input logic[1:0] expected_response,input int stall=0);
    for(int beat=0;beat<=int'(value.len);beat++) begin
      int timeout;
      logic[255:0] expected_data;
      lpddr5_mc_axi_read_t saved;
      longint unsigned at,limit;
      timeout=0;
      while(!r_valid[p])begin tick();timeout++; if(timeout>4000)$fatal(1,"R timeout");end
      expected_data=0;at=oracle_at(value,beat);limit=(at/(64'd1<<value.size)+1)*(64'd1<<value.size);
      if(expected_response==0)for(int lane=0;lane<BUS_BYTES;lane++)if(at+64'(lane)<limit)
        expected_data[int'((at+64'(lane))%64'(BUS_BYTES))*8+:8]=expected_memory[16'(at+64'(lane))];
      if(r_generation[p]!=generation[p][slot]||r[p].id!=value.id||r[p].last!=(beat==int'(value.len))||r[p].response!=expected_response
          ||r[p].data!==expected_data||r_slot[p]!=6'(slot))
        $fatal(1,"R mismatch beat%0d p%0d slot%0d got%h expect%h resp%0d/%0d",beat,p,slot,r[p].data,expected_data,r[p].response,expected_response);
      saved=r[p];
      repeat(stall)begin tick();if(!r_valid[p]||r[p]!==saved)$fatal(1,"stalled R changed");end
      @(negedge clk);r_ready[p]=1;tick();@(negedge clk);r_ready[p]=0;
    end
    event_send(p,slot,0,1,1,expected_response);generation[p][slot]++;
  endtask
  task automatic wait_settled;
    int timeout;
    timeout=0;
    while(!settled)begin tick();timeout++; if(timeout>1000)$fatal(1,"settlement timeout");end
  endtask
  task automatic note(input string name);
    tests++;$display("PASS core %s",name);
  endtask
  task automatic reset_core(input bit posted=0);
    @(negedge clk);rst_n=0;enable=0;fatal=0;recover=0;cfg_valid=0;
    aw_valid=0;ar_valid=0;w_valid=0;b_ready=0;r_ready=0;event_valid=0;
    for(int p=0;p<PORTS;p++)begin
      source_sequence[p]=0;
      for(int s=0;s<DEPTH;s++)generation[p][s]=1;
    end
    for(int c=0;c<CHANNELS;c++)begin busy[c]=0;sent_physical[c]=0;delay_count[c]=0;completion[c]='0;end
    tick(3);@(negedge clk);rst_n=1;tick(3);
    cfg='0;cfg.granule64=1;cfg.posted=posted;cfg.boot_epoch=7;cfg.config_epoch=11;
    for(int p=0;p<PORTS;p++)begin cfg.read_limit[p]=8'(DEPTH);cfg.write_limit[p]=8'(DEPTH);end
    cfg.regions[0].enable=1;cfg.regions[0].size=65536;
    for(int j=0;j<40;j++)cfg.regions[0].permutation[j]=6'(j);
    @(negedge clk);cfg_valid=1;if(!cfg_ready)$fatal(1,"configuration not ready");tick();@(negedge clk);cfg_valid=0;tick();
    if(!config_valid||fault)$fatal(1,"configuration rejected");
    @(negedge clk);enable=1;
  endtask
  task automatic configure_current;
    @(negedge clk);enable=0;tick(2);
    @(negedge clk);cfg_valid=1;
    if(!cfg_ready)$fatal(1,"settled configuration not ready");
    tick();@(negedge clk);cfg_valid=0;tick(2);
  endtask
  task automatic manual_complete(input int index,input bit stale,physical,integrity);
    @(negedge clk);completion[0]='0;
    completion[0].identity.tag=manual_granules[index].tag;
    completion[0].identity.generation=manual_granules[index].generation;
    completion[0].identity.boot_epoch=cfg.boot_epoch+32'(stale);
    completion[0].identity.config_epoch=cfg.config_epoch;
    completion[0].is_write=manual_granules[index].is_write;
    completion[0].physical_done=physical;completion[0].integrity_done=integrity;
    for(int j=0;j<64;j++)completion[0].data[j*8+:8]=actual_memory[16'(manual_granules[index].address)+16'(j)];
    completion_valid[0]=1;
    tick();@(negedge clk);completion_valid[0]=0;
  endtask

  initial begin : test
    lpddr5_mc_axi_address_t a, overlap, invalid;
    int before_requests, before_integrity, before_physical;
    for(int i=0;i<65536;i++)begin actual_memory[i]=8'(i*29+7);expected_memory[i]=actual_memory[i];end
    for(int p=0;p<PORTS;p++)begin aw[p]='0;ar[p]='0;w[p]='0;response_event[p]='0;end
    reset_core();
    a=make_address('h100,64/BUS_BYTES,$clog2(BUS_BYTES),1,3);
    admit(0,0,1,a);send_w(0,0,a);receive_b(0,0,0,4);
    admit(0,0,0,a);receive_r(0,0,a,0,2);wait_settled();note("INCR64 coalescing/data/backpressure");
    a=make_address('h173,5,2,1,4);
    admit(0,1,1,a);send_w(0,1,a);receive_b(0,1,0);
    admit(0,1,0,a);receive_r(0,1,a,0);wait_settled();note("narrow unaligned lanes");
    a=make_address('h234,4,2,2,5);
    admit(1,2,1,a);send_w(1,2,a);receive_b(1,2,0);
    admit(1,2,0,a);receive_r(1,2,a,0);wait_settled();note("WRAP independent oracle");
    a=make_address('h307,3,1,0,6);
    admit(0,3,1,a);send_w(0,3,a);receive_b(0,3,0);
    admit(0,3,0,a);receive_r(0,3,a,0);wait_settled();note("FIXED overwrite order");
    a=make_address('h400,64/BUS_BYTES,$clog2(BUS_BYTES),1,7);before_requests=requests_seen;
    admit(0,4,1,a);send_w(0,4,a,0,0);receive_b(0,4,2);
    if(requests_seen!=before_requests)$fatal(1,"malformed WLAST caused side effect");wait_settled();note("early WLAST full drain no side effect");
    a=make_address('h440,64/BUS_BYTES,$clog2(BUS_BYTES),1,22);before_requests=requests_seen;
    admit(0,4,1,a);send_w(0,4,a,2,0);receive_b(0,4,2);
    if(requests_seen!=before_requests)$fatal(1,"missing final WLAST caused side effect");wait_settled();note("missing final WLAST full drain no side effect");
    a=make_address('h480,64/BUS_BYTES+1,$clog2(BUS_BYTES),1,23);
    admit(0,4,1,a);send_w(0,4,a,3);receive_b(0,4,0);
    admit(0,4,0,a);receive_r(0,4,a,0);wait_settled();note("partial strobes preserve bytes across granules");
    a=make_address('h501,2,1,1,8);before_requests=requests_seen;
    admit(0,5,1,a);send_w(0,5,a,1,0);receive_b(0,5,2);
    if(requests_seen!=before_requests)$fatal(1,"invalid strobes caused side effect");wait_settled();note("invalid strobe no side effect");
    invalid=make_address('hff0,2,4,1,9);before_requests=requests_seen;
    admit(0,6,0,invalid);receive_r(0,6,invalid,3);wait_settled();
    if(requests_seen!=before_requests)$fatal(1,"4K error issued request");note("DECERR complete read");
    invalid=make_address('h10000,3,4,1,10);before_requests=requests_seen;
    admit(0,7,1,invalid);send_w(0,7,invalid,-1,0);receive_b(0,7,3);wait_settled();
    if(requests_seen!=before_requests)$fatal(1,"unmapped write side effect");note("unmapped write consumes W");
    a=make_address('h600,64/BUS_BYTES,$clog2(BUS_BYTES),1,11);overlap=a;overlap.id=12;
    admit(0,8,1,a);admit(1,8,0,overlap);before_requests=requests_seen;tick(20);
    if(requests_seen!=before_requests)$fatal(1,"AW-before-W hazard bypass");
    send_w(0,8,a);receive_b(0,8,0);receive_r(1,8,overlap,0);wait_settled();note("crossport AW-before-W hazard");
    a=make_address('h800,256,3,1,13);
    admit(0,9,1,a);send_w(0,9,a);receive_b(0,9,0);
    admit(0,9,0,a);receive_r(0,9,a,0);wait_settled();note("256-beat bounded tracking");
    a=make_address('h1600,1,$clog2(BUS_BYTES),1,14);a.lock=1;
    admit(0,10,1,a);send_w(0,10,a);receive_b(0,10,0);wait_settled();note("exclusive ordinary OKAY");
    stale_first=1;a=make_address('h1680,1,$clog2(BUS_BYTES),1,20);
    admit(0,10,0,a);receive_r(0,10,a,0);wait_settled();
    if(stale_count!=1)$fatal(1,"stale completion not rejected/count mismatch");
    stale_first=0;note("stale boot epoch cannot complete current request");
    // A terminal correlated error has a known disposition. Keep configuration
    // and unrelated admission live so software can rewrite a poisoned word.
    fail_next=1;a=make_address('h16c0,64/BUS_BYTES,$clog2(BUS_BYTES),1,24);
    admit(0,10,0,a);receive_r(0,10,a,2);wait_settled();
    if(fault || !config_valid)$fatal(1,"correlated read error invented global ownership loss");
    fail_next=0;admit(0,10,1,a);send_w(0,10,a);receive_b(0,10,0);
    admit(0,10,0,a);receive_r(0,10,a,0);wait_settled();
    note("correlated channel error settles locally and permits protected rewrite");
    // Fatal before W: all admitted beats are still consumed, one B is emitted.
    a=make_address('h1700,64/BUS_BYTES,$clog2(BUS_BYTES),1,15);admit(0,11,1,a);
    @(negedge clk);fatal=1;tick(3);send_w(0,11,a,-1,0);receive_b(0,11,2);wait_settled();
    if(!fault)$fatal(1,"fatal not latched");note("fatal remaining W settlement");
    reset_core();integrity_first=1;
    a=make_address('h1780,64/BUS_BYTES,$clog2(BUS_BYTES),1,18);
    before_integrity=integrity_completions;
    admit(0,12,1,a);send_w(0,12,a);
    while(integrity_completions==before_integrity)tick();
    tick(2);if(b_valid[0])$fatal(1,"integrity-only completion exposed B before physical witness");
    receive_b(0,12,0);wait_settled();integrity_first=0;note("integrity-first completion waits physical witness");
    reset_core();auto_channel=0;
    a=make_address('h1800,3,$clog2(BUS_BYTES),1,16);admit(1,12,0,a);tick(10);
    @(negedge clk);fatal=1;receive_r(1,12,a,2);wait_settled();note("fatal read complete zero-data settlement");
    auto_channel=1;reset_core();auto_channel=0;
    for(int slot=0;slot<DEPTH;slot++)begin
      a=make_address('h2000+slot*64,1,$clog2(BUS_BYTES),1,21);
      admit(0,slot,0,a);
    end
    @(negedge clk);ar[0]='0;ar[0].axi=a;ar[0].slot=0;ar[0].generation=2;
    ar[0].sequence_number=source_sequence[0];ar_valid[0]=1;tick(8);
    if(ar_ready[0])$fatal(1,"exhausted response credit admitted reused slot");
    @(negedge clk);ar_valid[0]=0;fatal=1;
    for(int slot=0;slot<DEPTH;slot++)begin
      a=make_address('h2000+slot*64,1,$clog2(BUS_BYTES),1,21);
      receive_r(0,slot,a,2);
    end
    wait_settled();note("response credit exhaustion and complete fatal retirement");
    auto_channel=1;reset_core(1);posted_delay=1;
    a=make_address('h1900,64/BUS_BYTES,$clog2(BUS_BYTES),1,17);admit(0,13,1,a);send_w(0,13,a);
    receive_b(0,13,0);tick(30);wait_settled();note("posted physical eligibility and delayed integrity");
    if(late_error)$fatal(1,"late error without failing witness");
    reset_core(1);before_physical=physical_completions;
    a=make_address('h1a00,64/BUS_BYTES,$clog2(BUS_BYTES),1,19);
    admit(0,14,1,a);send_w(0,14,a);
    while(physical_completions==before_physical)tick();
    if(EARLY_POSTED)begin
      receive_b(0,14,0);fail_next=1;tick(30);wait_settled();
      if(!late_error)$fatal(1,"posted exposed success lost late integrity failure");
      note("posted exposed success retains late fault");
    end else begin
      tick(2);if(b_valid[0])$fatal(1,"safe posted transport exposed before integrity");
      fail_next=1;receive_b(0,14,2);tick(10);wait_settled();
      if(late_error)$fatal(1,"unoffered failure incorrectly became late error");
      note("safe posted transport fails unoffered response");
    end
    fail_next=0;posted_delay=0;
    reset_core();
    a=make_address('h1b00,1,$clog2(BUS_BYTES),1,24);
    admit(0,15,1,a);send_w(0,15,a);
    begin
      lpddr5_mc_axi_b_t exposed_b;
      int timeout;
      timeout=0;
      while(!b_valid[0])begin tick();timeout++;if(timeout>1000)$fatal(1,"exposed B timeout");end
      exposed_b=b[0];
      event_send(0,15,1,1,0,0);
      @(negedge clk);fatal=1;tick(3);
      if(!b_valid[0] || b[0]!==exposed_b || b[0].response!=0)
        $fatal(1,"fatal changed already exposed stalled B");
      if(!late_error)$fatal(1,"exposed successful B lost fatal diagnostic");
      receive_b(0,15,0);wait_settled();note("exposed B stays stable through fatal and handshake");
    end
    reset_core();auto_channel=0;manual_channel=1;manual_requests=0;
    for(int slot=0;slot<DEPTH;slot++)begin
      a=make_address('h3000+slot*128,1,$clog2(BUS_BYTES),1,25);
      admit(0,slot,0,a);
    end
    for(int slot=0;slot<2;slot++)begin
      a=make_address('h3000+(DEPTH+slot)*128,1,$clog2(BUS_BYTES),1,25);
      admit(1,slot,0,a);
    end
    tick(50);
    if(manual_requests!=DEPTH)$fatal(1,"channel granule credits admitted %0d expected%0d",manual_requests,DEPTH);
    manual_complete(0,1,1,1);tick(10);
    if(manual_requests!=DEPTH || stale_count!=1)$fatal(1,"stale completion released channel credit");
    manual_complete(0,0,1,0);tick(10);
    if(manual_requests!=DEPTH)$fatal(1,"physical-only completion released channel credit");
    manual_complete(0,0,1,1);tick(10);
    if(manual_requests!=DEPTH+1)$fatal(1,"matched final completion did not reopen exactly one credit");
    tick(20);if(manual_requests!=DEPTH+1)$fatal(1,"channel credit reopened twice");
    a=make_address('h3000,1,$clog2(BUS_BYTES),1,25);receive_r(0,0,a,0);
    @(negedge clk);fatal=1;
    for(int slot=1;slot<DEPTH;slot++)begin
      a=make_address('h3000+slot*128,1,$clog2(BUS_BYTES),1,25);receive_r(0,slot,a,2);
    end
    for(int slot=0;slot<2;slot++)begin
      a=make_address('h3000+(DEPTH+slot)*128,1,$clog2(BUS_BYTES),1,25);receive_r(1,slot,a,2);
    end
    wait_settled();manual_channel=0;auto_channel=1;note("channel granule credits require matching physical and integrity completion");
    reset_core();
    a=make_address('h5800,1,$clog2(BUS_BYTES),1,26);
    admit(0,0,0,a);receive_r(0,0,a,0);wait_settled();
    cfg.config_epoch++;configure_current();
    if(!config_valid || fault)$fatal(1,"same-boot configuration epoch rejected");
    @(negedge clk);enable=1;
    admit(0,0,0,a);receive_r(0,0,a,0);wait_settled();
    cfg.boot_epoch++;cfg.config_epoch=1;configure_current();
    if(!config_valid || fault)$fatal(1,"new boot epoch rejected");
    for(int p=0;p<PORTS;p++)source_sequence[p]=0;
    @(negedge clk);enable=1;
    admit(0,0,0,a);receive_r(0,0,a,0);wait_settled();note("configuration epoch retains sequence and boot epoch restarts sequence");
    @(negedge clk);fatal=1;tick(2);
    @(negedge clk);fatal=0;enable=0;recover=1;tick();@(negedge clk);recover=0;tick();
    cfg.config_epoch++;configure_current();
    if(config_valid || !fault)$fatal(1,"recovery accepted stale boot epoch");
    @(negedge clk);recover=1;tick();@(negedge clk);recover=0;tick();
    cfg.boot_epoch++;configure_current();
    if(!config_valid || fault)$fatal(1,"recovery rejected fresh boot epoch");
    for(int p=0;p<PORTS;p++)source_sequence[p]=0;
    @(negedge clk);enable=1;
    admit(0,0,0,a);receive_r(0,0,a,0);wait_settled();note("fatal recovery requires fresh boot epoch before service");

    $display("CORE_RESULT tests=%0d requests=%0d physical=%0d integrity=%0d exposure=%0d handshake=%0d",tests,requests_seen,physical_completions,integrity_completions,feedback_exposures,feedback_handshakes);
    $finish;
  end
  initial begin #2000000;$fatal(1,"global timeout");end
endmodule
