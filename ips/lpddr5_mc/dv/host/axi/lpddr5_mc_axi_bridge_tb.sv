`timescale 1ns/1ps
/* verilator lint_off BLKSEQ */
module lpddr5_mc_axi_bridge_tb #(
  parameter int DATA_BITS=128,
  parameter int DEPTH=16,
  parameter int ID_BITS=16,
  parameter int ADDRESS_BITS=40
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_host_pkg::*;
  logic core_clk=0, core_rst_n=0, enable=0, fatal=0, rearm=0;
  logic rearm_ready, initialized, fault, settled, abort_settled, core_settled, admission_busy;
  logic axi_clk0=0,axi_clk1=0;
  logic axi_clock_run0=1,axi_clock_run1=1;
  wire [1:0] axi_clk={axi_clk1,axi_clk0};
  logic [1:0] axi_rst_n=0;
  logic [1:0] awvalid=0, arvalid=0, wvalid=0, awready, arready, wready;
  logic [1:0] bvalid,rvalid,bready=3,rready=3,late_error;
  lpddr5_mc_axi_address_t [1:0] aw,ar;
  lpddr5_mc_axi_write_t [1:0] wd;
  lpddr5_mc_axi_b_t [1:0] b;
  lpddr5_mc_axi_read_t [1:0] r;
  lpddr5_mc_host_config_t cfg;
  logic [1:0][1:0][DEPTH-1:0] slot_available;
  logic [1:0] cawvalid,carvalid,cwvalid,cawready,carready,cwready;
  lpddr5_mc_host_address_t [1:0] caw,car;
  lpddr5_mc_host_write_t [1:0] cw;
  logic [1:0] cbvalid=0,crvalid=0,cbready,crready;
  lpddr5_mc_axi_b_t [1:0] cb;
  lpddr5_mc_axi_read_t [1:0] cr;
  logic [1:0][5:0] cbslot,crslot;
  logic [1:0][15:0] cbgen,crgen;
  logic [1:0] retirevalid,retireready,eventvalid,eventready=3;
  lpddr5_mc_host_response_event_t [1:0] retire,event_data;
  logic [63:0] accepted_addresses,grant_cycles;
  logic automatic_responses=1, allow_address_imports=1, manual_retirement=0;
  lpddr5_mc_host_response_event_t injected_retire='0;
  lpddr5_mc_axi_read_t duplicate_read='0;
  logic[5:0] duplicate_slot=0;
  logic[15:0] duplicate_generation=0;
  int injected_retirements_consumed=0;
  int stage=0;
  always #0.625 core_clk=~core_clk;
  always #1.25 if(axi_clock_run0)axi_clk0=~axi_clk0;
  initial begin #0.375; forever #1.25 if(axi_clock_run1)axi_clk1=~axi_clk1; end

  lpddr5_mc_axi_bridge #(.DATA_BITS(DATA_BITS),.ID_BITS(ID_BITS),.ADDRESS_BITS(ADDRESS_BITS),.DEPTH(DEPTH)) dut (
    .core_clk_i(core_clk),.core_rst_ni(core_rst_n),.enable_i(enable),.fatal_i(fatal),.core_settled_i(core_settled),
    .rearm_i(rearm),.rearm_ready_o(rearm_ready),.initialized_o(initialized),.fault_o(fault),.host_settled_o(settled),
    .admission_busy_o(admission_busy),
    .abort_settled_o(abort_settled),
    .cfg_i(cfg),.cfg_valid_i(1'b1),.axi_clk_i(axi_clk),.axi_rst_ni(axi_rst_n),
    .awvalid_i(awvalid),.arvalid_i(arvalid),.wvalid_i(wvalid),.awready_o(awready),.arready_o(arready),.wready_o(wready),
    .aw_i(aw),.ar_i(ar),.w_i(wd),.bvalid_o(bvalid),.rvalid_o(rvalid),.bready_i(bready),.rready_i(rready),.b_o(b),.r_o(r),
    .late_error_o(late_error),.core_slot_available_i(slot_available),
    .core_awvalid_o(cawvalid),.core_arvalid_o(carvalid),.core_wvalid_o(cwvalid),
    .core_awready_i(cawready),.core_arready_i(carready),.core_wready_i(cwready),.core_aw_o(caw),.core_ar_o(car),.core_w_o(cw),
    .core_bvalid_i(cbvalid),.core_rvalid_i(crvalid),.core_bready_o(cbready),.core_rready_o(crready),
    .core_b_i(cb),.core_r_i(cr),.core_bslot_i(cbslot),.core_rslot_i(crslot),.core_bgeneration_i(cbgen),.core_rgeneration_i(crgen),
    .core_retire_valid_i(retirevalid),.core_retire_ready_o(retireready),.core_retire_i(retire),
    .core_event_valid_o(eventvalid),.core_event_ready_i(eventready),.core_event_o(event_data),
    .accepted_addresses_o(accepted_addresses),.grant_cycles_o(grant_cycles)
  );
  typedef struct packed {
    logic valid,sent,acknowledged;
    lpddr5_mc_host_address_t address;
    logic [8:0] wcount,rindex;
  } model_t;
  model_t [1:0][DEPTH-1:0] model_w,model_r;
  typedef struct packed {logic port_index,write_access;logic [15:0] id;lpddr5_mc_axi_address_t payload;logic [31:0] seq;} admission_t;
  int unsigned source_seq[2]='{default:0};
  admission_t actual_order[$];
  lpddr5_mc_axi_address_t expected_w0[$],expected_w1[$],expected_r0[$],expected_r1[$];
  int unsigned rindex0=0,rindex1=0, source_writes=0,source_reads=0,source_b=0,source_r=0;
  int unsigned imports=0,aborted_imports=0,model_exposures=0,model_handshakes=0;
  realtime first_accept,last_accept;
  logic measure_window=0;
  longint unsigned measured_ck=0,idle_ck=0,grant_ck=0,accept_ck=0,proposal_ck=0,await_source_ck=0,import_stall_ck=0;
  longint unsigned measured_axi=0,awvalid_ck=0,awready_ck=0,arvalid_ck=0,arready_ck=0,wvalid_ck=0,wready_ck=0;
  longint unsigned await_grant_axi=0,accept_fifo_full_axi=0,w_fifo_full_axi=0;
  longint unsigned max_local_occupancy=0;

  function automatic lpddr5_mc_axi_address_t address(input int unsigned id,input int unsigned at,input int unsigned beats);
    lpddr5_mc_axi_address_t a;
    assert(id<65536)else $fatal(1,"test ID overflow");
    a='0;a.id=16'(id&((1<<ID_BITS)-1));a.address=40'(at);a.len=8'(beats-1);a.size=3'($clog2(DATA_BITS/8));a.burst=1;
    return a;
  endfunction
  function automatic logic [255:0] read_pattern(input logic[15:0] id,input logic[8:0] beat);
    return (256'(id)<<32)|256'(beat);
  endfunction
  for(genvar p=0;p<2;p++) begin:monitors
    always @(posedge axi_clk[p]) begin
      admission_t x;
      lpddr5_mc_axi_address_t a;
      logic unused_address;
      if(axi_rst_n[p]) begin
        if(measure_window)begin
          measured_axi++;awvalid_ck+=64'(awvalid[p]);awready_ck+=64'(awready[p]);
          arvalid_ck+=64'(arvalid[p]);arready_ck+=64'(arready[p]);
          wvalid_ck+=64'(wvalid[p]);wready_ck+=64'(wready[p]);
          await_grant_axi+=64'(dut.ports[p].endpoint.candidate_valid_q&&!dut.ports[p].endpoint.granted_q);
          accept_fifo_full_axi+=64'(dut.ports[p].endpoint.granted_q&&!dut.ports[p].endpoint.accepted_local_ready);
          w_fifo_full_axi+=64'(wvalid[p]&&dut.ports[p].endpoint.w_owner>=0&&!dut.ports[p].endpoint.w_local_ready);
        end
        if(awvalid[p]&&awready[p]) begin
          x.port_index=1'(p);x.write_access=1;x.id=aw[p].id;x.payload=aw[p];x.seq=source_seq[p];source_seq[p]++;actual_order.push_back(x);source_writes++;
          if(p==0) expected_w0.push_back(aw[p]);else expected_w1.push_back(aw[p]);
          if(first_accept==0) first_accept=$realtime;last_accept=$realtime;
        end
        if(arvalid[p]&&arready[p]) begin
          x.port_index=1'(p);x.write_access=0;x.id=ar[p].id;x.payload=ar[p];x.seq=source_seq[p];source_seq[p]++;actual_order.push_back(x);source_reads++;
          if(p==0) expected_r0.push_back(ar[p]);else expected_r1.push_back(ar[p]);
          if(first_accept==0) first_accept=$realtime;last_accept=$realtime;
        end
        if(bvalid[p]&&bready[p]) begin
          assert((p==0?expected_w0.size():expected_w1.size())>0)else $fatal(1,"unsolicited B");
          if(p==0)a=expected_w0.pop_front();else a=expected_w1.pop_front();
          unused_address=^a;
          assert(b[p].id==a.id)else $fatal(1,"B ID/order");source_b++;
        end
        if(rvalid[p]&&rready[p]) begin
          assert((p==0?expected_r0.size():expected_r1.size())>0)else $fatal(1,"unsolicited R");
          if(p==0)a=expected_r0[0];else a=expected_r1[0];
          assert(r[p].id==a.id&&r[p].last==((p==0?rindex0:rindex1)==int'(a.len)))else $fatal(1,"R count/ID/RLAST");
          if(r[p].response==0)assert(r[p].data==read_pattern(a.id,9'(p==0?rindex0:rindex1)))else $fatal(1,"R payload");
          else assert(r[p].data==0)else $fatal(1,"error R nonzero");
          source_r++;
          if(r[p].last) begin
            if(p==0)begin a=expected_r0.pop_front();rindex0=0;end
            else begin a=expected_r1.pop_front();rindex1=0;end
          end else if(p==0)rindex0++;else rindex1++;
        end
      end
    end
    assert property(@(posedge axi_clk[p])disable iff(!axi_rst_n[p]) bvalid[p]&&!bready[p]|=>bvalid[p]&&$stable(b[p]));
    assert property(@(posedge axi_clk[p])disable iff(!axi_rst_n[p]) rvalid[p]&&!rready[p]|=>rvalid[p]&&$stable(r[p]));
  end
  always @(posedge core_clk)begin
    if(measure_window)begin
      int occupancy;
      occupancy=dut.ports[0].endpoint.write_count+dut.ports[0].endpoint.read_count
        +dut.ports[1].endpoint.write_count+dut.ports[1].endpoint.read_count;
      measured_ck++;idle_ck+=64'(dut.state_q==0);grant_ck+=64'(dut.state_q==3);
      accept_ck+=64'(dut.state_q==4);proposal_ck+=64'(|dut.proposal_valid);
      await_source_ck+=64'(dut.state_q==4&&!dut.ack_received_q[dut.selected_q]);
      import_stall_ck+=64'((|(dut.accepted_valid & ~dut.accepted_ready)));
      if(64'(occupancy)>max_local_occupancy)max_local_occupancy=64'(occupancy);
    end
  end
  always @(posedge axi_clk0)
    if(dut.ports[0].endpoint.retire_local_valid&&dut.ports[0].endpoint.retire_local_ready
        &&dut.ports[0].endpoint.retire_local==injected_retire&&injected_retire.response!=0)
      injected_retirements_consumed++;
  always_comb begin
    slot_available='0;core_settled=1;retirevalid=0;retire='0;cwready=0;
    cawready={2{(enable||admission_busy)&&allow_address_imports}};carready={2{(enable||admission_busy)&&allow_address_imports}};
    for(int p=0;p<2;p++)begin
      if(int'(cw[p].slot)<DEPTH)cwready[p]=model_w[p][$clog2(DEPTH)'(cw[p].slot)].valid
        &&model_w[p][$clog2(DEPTH)'(cw[p].slot)].address.generation==cw[p].generation;
      for(int i=0;i<DEPTH;i++)begin
        slot_available[p][0][i]=!model_w[p][i].valid;slot_available[p][1][i]=!model_r[p][i].valid;
        core_settled&=!model_w[p][i].valid&&!model_r[p][i].valid;
        if(!retirevalid[p]&&model_w[p][i].valid&&model_w[p][i].acknowledged)begin
          retirevalid[p]=1;retire[p].slot=6'(i);retire[p].generation=model_w[p][i].address.generation;retire[p].write_response=1;
        end
        if(!retirevalid[p]&&model_r[p][i].valid&&model_r[p][i].acknowledged)begin
          retirevalid[p]=1;retire[p].slot=6'(i);retire[p].generation=model_r[p][i].address.generation;
        end
      end
    end
    if(manual_retirement)begin retirevalid[0]=1;retire[0]=injected_retire;end
  end
  always @(posedge core_clk or negedge core_rst_n)begin
    if(!core_rst_n)begin
      for(int p=0;p<2;p++)for(int i=0;i<DEPTH;i++)begin model_w[p][i]<='0;model_r[p][i]<='0;end
      cbvalid<=0;crvalid<=0;cb<='0;cr<='0;cbslot<=0;crslot<=0;cbgen<=0;crgen<=0;
    end else begin
      for(int p=0;p<2;p++)begin
        if(cawvalid[p]&&cawready[p]||carvalid[p]&&carready[p])begin
          admission_t expected;
          lpddr5_mc_host_address_t a;
          logic write_access;
          write_access=cawvalid[p];a=write_access?caw[p]:car[p];
          assert(actual_order.size()>0)else $fatal(1,"core imported before source acceptance");
          begin
            int found;
            found=-1;
            for(int i=0;i<actual_order.size();i++)
              if(found<0&&actual_order[i].port_index==1'(p))found=i;
            assert(found>=0)else $fatal(1,"source import without same-port acceptance");
            expected=actual_order[found];
            assert(expected.write_access==write_access&&expected.payload==a.axi&&expected.seq==a.sequence_number)
              else $fatal(1,"per-source payload/sequence order not preserved");
            for(int i=0;i<found;i++)begin
              // Independent exact byte intervals, not the implementation's
              // hash mask. All fixture requests here are aligned INCR.
              assert(41'(actual_order[i].payload.address)+(41'(actual_order[i].payload.len)+1)*(41'd1<<actual_order[i].payload.size)
                  <=41'(a.axi.address)
                  ||41'(a.axi.address)+(41'(a.axi.len)+1)*(41'd1<<a.axi.size)<=41'(actual_order[i].payload.address))
                else $fatal(1,"overlapping cross-port source acceptance order violated");
            end
            actual_order.delete(found);
          end
          assert(slot_available[p][write_access?0:1][$clog2(DEPTH)'(a.slot)])else $fatal(1,"core credit not reserved");
          if(write_access)begin model_w[p][$clog2(DEPTH)'(a.slot)]<='0;model_w[p][$clog2(DEPTH)'(a.slot)].valid<=1;model_w[p][$clog2(DEPTH)'(a.slot)].address<=a;end
          else begin model_r[p][$clog2(DEPTH)'(a.slot)]<='0;model_r[p][$clog2(DEPTH)'(a.slot)].valid<=1;model_r[p][$clog2(DEPTH)'(a.slot)].address<=a;end
          imports++;
        end
        if(cwvalid[p]&&cwready[p])begin
          assert(model_w[p][$clog2(DEPTH)'(cw[p].slot)].valid&&model_w[p][$clog2(DEPTH)'(cw[p].slot)].address.generation==cw[p].generation)
            else $fatal(1,"W preceded imported AW or lost identity");
          model_w[p][$clog2(DEPTH)'(cw[p].slot)].wcount<=model_w[p][$clog2(DEPTH)'(cw[p].slot)].wcount+1;
        end
        if(cbvalid[p]&&cbready[p])cbvalid[p]<=0;
        if(crvalid[p]&&crready[p])crvalid[p]<=0;
        if(automatic_responses&&!fatal)begin
          int wb,rb;
          wb=-1;rb=-1;
          for(int i=0;i<DEPTH;i++)begin
            if(model_w[p][i].valid&&!model_w[p][i].sent&&int'(model_w[p][i].wcount)>int'(model_w[p][i].address.axi.len)
                &&(wb<0||model_w[p][i].address.sequence_number<model_w[p][wb].address.sequence_number))wb=i;
            if(model_r[p][i].valid&&!model_r[p][i].sent
                &&(rb<0||model_r[p][i].address.sequence_number<model_r[p][rb].address.sequence_number))rb=i;
          end
          if((!cbvalid[p]||cbready[p])&&wb>=0)begin
            cbvalid[p]<=1;cb[p].id<=model_w[p][wb].address.axi.id;cb[p].response<=0;
            cbslot[p]<=6'(wb);cbgen[p]<=model_w[p][wb].address.generation;model_w[p][wb].sent<=1;
          end
          if((!crvalid[p]||crready[p])&&rb>=0)begin
            crvalid[p]<=1;cr[p].id<=model_r[p][rb].address.axi.id;cr[p].response<=0;
            cr[p].data<=read_pattern(model_r[p][rb].address.axi.id,model_r[p][rb].rindex);
            cr[p].last<=model_r[p][rb].rindex=={1'b0,model_r[p][rb].address.axi.len};
            crslot[p]<=6'(rb);crgen[p]<=model_r[p][rb].address.generation;
            if(model_r[p][rb].rindex=={1'b0,model_r[p][rb].address.axi.len})model_r[p][rb].sent<=1;
            else model_r[p][rb].rindex<=model_r[p][rb].rindex+1;
          end
        end
        if(eventvalid[p]&&eventready[p])begin
          if(event_data[p].exposed)model_exposures++;
          if(event_data[p].handshaken)begin
            model_handshakes++;
            if(event_data[p].write_response)model_w[p][$clog2(DEPTH)'(event_data[p].slot)].acknowledged<=1;
            else model_r[p][$clog2(DEPTH)'(event_data[p].slot)].acknowledged<=1;
          end
        end
        if(retirevalid[p]&&retireready[p]&&!(manual_retirement&&p==0))begin
          if(retire[p].write_response)model_w[p][$clog2(DEPTH)'(retire[p].slot)].valid<=0;
          else model_r[p][$clog2(DEPTH)'(retire[p].slot)].valid<=0;
        end
      end
    end
  end
  task automatic send_aw(input int p,input int id,input int at,input int beats);
    if(p==0)@(negedge axi_clk0);else @(negedge axi_clk1);
    aw[p]=address(id,at,beats);awvalid[p]=1;
    do begin if(p==0)@(posedge axi_clk0);else @(posedge axi_clk1);end while(!awready[p]);
    if(p==0)@(negedge axi_clk0);else @(negedge axi_clk1);
    awvalid[p]=0;
  endtask
  task automatic send_ar(input int p,input int id,input int at,input int beats);
    if(p==0)@(negedge axi_clk0);else @(negedge axi_clk1);
    ar[p]=address(id,at,beats);arvalid[p]=1;
    do begin if(p==0)@(posedge axi_clk0);else @(posedge axi_clk1);end while(!arready[p]);
    if(p==0)@(negedge axi_clk0);else @(negedge axi_clk1);
    arvalid[p]=0;
  endtask
  task automatic send_w(input int p,input int beats,input int start=0);
    for(int i=start;i<beats;i++)begin
      if(p==0)@(negedge axi_clk0);else @(negedge axi_clk1);
      wd[p]='0;wd[p].data=256'(i);wd[p].strobe=32'((64'd1<<(DATA_BITS/8))-1);wd[p].last=i==beats-1;wvalid[p]=1;
      do begin if(p==0)@(posedge axi_clk0);else @(posedge axi_clk1);end while(!wready[p]);
      if(p==0)@(negedge axi_clk0);else @(negedge axi_clk1);
      wvalid[p]=0;
    end
  endtask
  task automatic stream_aw(input int p,input int count,input int id_base);
    if(p==0)@(negedge axi_clk0);else @(negedge axi_clk1);
    awvalid[p]=1;
    for(int i=0;i<count;i++)begin
      aw[p]=address(id_base+i,(i%24)*128+p*64,64/(DATA_BITS/8));
      do begin if(p==0)@(posedge axi_clk0);else @(posedge axi_clk1);end while(!awready[p]);
      if(p==0)@(negedge axi_clk0);else @(negedge axi_clk1);
    end
    awvalid[p]=0;
  endtask
  task automatic stream_ar(input int p,input int count,input int id_base);
    if(p==0)@(negedge axi_clk0);else @(negedge axi_clk1);
    arvalid[p]=1;
    for(int i=0;i<count;i++)begin
      ar[p]=address(id_base+i,(i%24)*128+p*64,64/(DATA_BITS/8));
      do begin if(p==0)@(posedge axi_clk0);else @(posedge axi_clk1);end while(!arready[p]);
      if(p==0)@(negedge axi_clk0);else @(negedge axi_clk1);
    end
    arvalid[p]=0;
  endtask
  task automatic stream_w(input int p,input int count);
    int beats;
    beats=64/(DATA_BITS/8);
    if(p==0)@(negedge axi_clk0);else @(negedge axi_clk1);
    wvalid[p]=1;
    for(int i=0;i<count*beats;i++)begin
      wd[p]='0;wd[p].data=256'(32'(i%beats));wd[p].strobe=32'((64'd1<<(DATA_BITS/8))-1);wd[p].last=(i%beats)==beats-1;
      do begin if(p==0)@(posedge axi_clk0);else @(posedge axi_clk1);end while(!wready[p]);
      if(p==0)@(negedge axi_clk0);else @(negedge axi_clk1);
    end
    wvalid[p]=0;
  endtask
  task automatic start_measurement;
    measured_ck=0;idle_ck=0;grant_ck=0;accept_ck=0;proposal_ck=0;await_source_ck=0;import_stall_ck=0;
    measured_axi=0;awvalid_ck=0;awready_ck=0;arvalid_ck=0;arready_ck=0;wvalid_ck=0;wready_ck=0;max_local_occupancy=0;
    await_grant_axi=0;accept_fifo_full_axi=0;w_fifo_full_axi=0;
    measure_window=1;
  endtask
  task automatic show_measurement;
    measure_window=0;
    $display("AXI_SATURATED_STATS CK=%0d idle=%0d grant_send=%0d grant_ack_wait=%0d proposal_present=%0d awaiting_source_CK=%0d import_stall_CK=%0d AXI_edges=%0d AWVALID=%0d AWREADY=%0d ARVALID=%0d ARREADY=%0d WVALID=%0d WREADY=%0d awaitgrant_AXI=%0d acceptfifo_full_AXI=%0d Wfifo_full_AXI=%0d max_ledger_occupancy=%0d",
      measured_ck,idle_ck,grant_ck,accept_ck,proposal_ck,await_source_ck,import_stall_ck,measured_axi,awvalid_ck,awready_ck,arvalid_ck,arready_ck,wvalid_ck,wready_ck,await_grant_axi,accept_fifo_full_axi,w_fifo_full_axi,max_local_occupancy);
  endtask
  task automatic send_stale_retirement(input logic write_response,input logic[5:0] slot,input logic[15:0] generation);
    int consumed_before;
    consumed_before=injected_retirements_consumed;
    @(negedge core_clk);
    injected_retire='0;injected_retire.write_response=write_response;injected_retire.slot=slot;
    injected_retire.generation=generation^16'h8000;injected_retire.response=2;
    injected_retire.exposed=!write_response;injected_retire.handshaken=!write_response;
    manual_retirement=1;
    do @(posedge core_clk);while(!retireready[0]);
    @(negedge core_clk);manual_retirement=0;
    while(injected_retirements_consumed==consumed_before)@(negedge axi_clk0);
    repeat(2)@(negedge axi_clk0);
  endtask
  task automatic recover(input logic fresh);
    aborted_imports+=int'(actual_order.size());actual_order.delete();
    enable=0;fatal=0;core_rst_n=0;
    repeat(8)@(negedge core_clk);
    if(fresh)begin cfg.boot_epoch++;source_seq[0]=0;source_seq[1]=0;end
    core_rst_n=1;
    while(!rearm_ready)@(negedge core_clk);
    rearm=1;@(negedge core_clk);rearm=0;
    if(fresh)begin while(!initialized)@(negedge core_clk);enable=1;end
  endtask
  task automatic wait_drained;
    while(!settled||!core_settled)@(negedge core_clk);
    repeat(30)@(negedge core_clk);
    assert(expected_w0.size()==0&&expected_w1.size()==0&&expected_r0.size()==0&&expected_r1.size()==0)
      else $fatal(1,"ledger settled before actual host responses");
  endtask
  initial begin
    realtime start_time,end_time,benchmark_start;
    int baseline,first_count,last_count;
    lpddr5_mc_axi_read_t held_read;
    lpddr5_mc_axi_b_t held_b;
    aw='0;ar='0;wd='0;cfg='0;cfg.boot_epoch=0;cfg.config_epoch=1;cfg.granule64=1;
    cfg.read_limit[0]=8'(DEPTH);cfg.read_limit[1]=8'(DEPTH);cfg.write_limit[0]=8'(DEPTH);cfg.write_limit[1]=8'(DEPTH);
    cfg.regions[0].enable=1;cfg.regions[0].base=0;cfg.regions[0].size=1<<30;
    for(int i=0;i<40;i++)cfg.regions[0].permutation[i]=6'(i);
    #20;axi_rst_n=3;core_rst_n=1;
    while(!rearm_ready)@(negedge core_clk);
    rearm=1;@(negedge core_clk);rearm=0;
    while(!initialized)@(negedge core_clk);enable=1;stage=1;
    fork
      begin send_aw(0,1,0,4);repeat(10)@(negedge axi_clk[0]);send_w(0,4);end
      begin send_ar(0,2,128,4);end
      begin send_ar(1,3,256,4);end
    join
    wait_drained();stage=2;
    // Quiesce after a preaccept proposal has crossed: core admission must stay
    // available until the granted source handshake has actually imported.
    fork
      begin send_ar(0,4,384,4);end
      begin
        while(dut.state_q==0)@(negedge core_clk);
        enable=0;
      end
    join
    while(admission_busy)@(negedge core_clk);
    assert(source_writes+source_reads==imports)else $fatal(1,"admission fence dropped before source import");
    repeat(10)@(negedge core_clk);
    assert(cawvalid==0&&carvalid==0)else $fatal(1,"quiesced source retained an admission");
    wait_drained();
    // An old idle level must not acknowledge a new disable generation while
    // either source clock is stopped, even after only one enabled core cycle.
    @(negedge axi_clk0);axi_clock_run0=0;
    @(negedge axi_clk1);axi_clock_run1=0;
    @(negedge core_clk);enable=1;
    @(negedge core_clk);enable=0;
    #0.001;assert(admission_busy&&!rearm_ready)else $fatal(1,"enable fall exposed stale idle/rearm readiness");
    rearm=1;@(negedge core_clk);rearm=0;
    assert(initialized)else $fatal(1,"unready enable-fall pulse started recovery");
    repeat(20)@(negedge core_clk);
    assert(admission_busy)else $fatal(1,"stopped source falsely acknowledged new disable generation");
    @(negedge core_clk);enable=1;
    @(negedge core_clk);enable=0;
    repeat(20)@(negedge core_clk);
    assert(admission_busy&&!rearm_ready)
      else $fatal(1,"second stopped-source disable aliased an old acknowledgement");
    axi_clock_run0=1;axi_clock_run1=1;
    while(admission_busy)@(negedge core_clk);
    enable=1;
    // Established rights can accept locally while the core cannot import.
    // A fresh disable ACK must wait for the actual acceptance FIFO reader,
    // even though the candidate and lease-control coordinator are both idle.
    allow_address_imports=0;
    send_ar(0,9,384,4);
    while(!carvalid[0])@(negedge core_clk);
    @(negedge core_clk);enable=0;
    repeat(40)@(negedge core_clk);
    assert(admission_busy&&carvalid[0]&&source_writes+source_reads==imports+1)
      else $fatal(1,"disable dropped accepted but unimported descriptor");
    allow_address_imports=1;
    while(admission_busy)@(negedge core_clk);
    assert(source_writes+source_reads==imports)else $fatal(1,"disable acknowledged before reader consumption");
    wait_drained();enable=1;

    baseline=int'(source_b);benchmark_start=$realtime;
    fork
      begin stream_aw(0,512,100);end
      begin stream_aw(1,512,700);end
      begin stream_w(0,512);end
      begin stream_w(1,512);end
      begin
        while(int'(source_b)<baseline+128)@(negedge core_clk);
        first_count=int'(source_b);start_time=$realtime;start_measurement();
        while(int'(source_b)<baseline+960)@(negedge core_clk);
        last_count=int'(source_b);end_time=$realtime;show_measurement();
        $display("AXI_SATURATED_PERF direction=write completed_bytes=%0d elapsed_ns=%0.3f GBps=%0.6f AXI_MHz=400 core_MHz=800",
          (last_count-first_count)*64,end_time-start_time,real'((last_count-first_count)*64)/(end_time-start_time));
      end
    join
    wait_drained();
    $display("AXI_STARTUP_AND_DRAIN direction=write completed_bytes=%0d elapsed_ns=%0.3f GBps=%0.6f warmup_transactions=128 measurement_transactions=832",
      (int'(source_b)-baseline)*64,$realtime-benchmark_start,real'((int'(source_b)-baseline)*64)/($realtime-benchmark_start));
    baseline=int'(source_r);benchmark_start=$realtime;
    fork
      begin stream_ar(0,512,1300);end
      begin stream_ar(1,512,1900);end
      begin
        while(int'(source_r)<baseline+128*(64/(DATA_BITS/8)))@(negedge core_clk);
        first_count=int'(source_r);start_time=$realtime;start_measurement();
        while(int'(source_r)<baseline+960*(64/(DATA_BITS/8)))@(negedge core_clk);
        last_count=int'(source_r);end_time=$realtime;show_measurement();
        $display("AXI_SATURATED_PERF direction=read completed_bytes=%0d elapsed_ns=%0.3f GBps=%0.6f AXI_MHz=400 core_MHz=800",
          (last_count-first_count)*(DATA_BITS/8),end_time-start_time,real'((last_count-first_count)*(DATA_BITS/8))/(end_time-start_time));
      end
    join
    wait_drained();
    $display("AXI_STARTUP_AND_DRAIN direction=read completed_bytes=%0d elapsed_ns=%0.3f GBps=%0.6f warmup_transactions=128 measurement_transactions=832",
      (int'(source_r)-baseline)*(DATA_BITS/8),$realtime-benchmark_start,real'((int'(source_r)-baseline)*(DATA_BITS/8))/($realtime-benchmark_start));
    stage=3;
    // Inject real stale retirement FIFO entries with a live same-slot owner.
    // Both the error and retirement state must ignore the wrong generation;
    // malformed feedback flags on that stale read record cannot fault it.
    bready[0]=0;send_aw(0,2400,0,4);send_w(0,4);
    while(!bvalid[0])@(negedge axi_clk0);
    send_stale_retirement(1,dut.ports[0].endpoint.b_held_q.slot,dut.ports[0].endpoint.b_held_q.generation);
    assert(!fault&&dut.ports[0].endpoint.writes[$clog2(DEPTH)'(dut.ports[0].endpoint.b_held_q.slot)].error==0
        &&!dut.ports[0].endpoint.writes[$clog2(DEPTH)'(dut.ports[0].endpoint.b_held_q.slot)].retired)
      else $fatal(1,"stale write retirement changed live owner");
    bready[0]=1;wait_drained();
    rready[0]=0;send_ar(0,2401,0,4);
    while(!rvalid[0])@(negedge axi_clk0);
    send_stale_retirement(0,dut.ports[0].endpoint.r_held_q.slot,dut.ports[0].endpoint.r_held_q.generation);
    assert(!fault&&dut.ports[0].endpoint.reads[$clog2(DEPTH)'(dut.ports[0].endpoint.r_held_q.slot)].error==0
        &&!dut.ports[0].endpoint.reads[$clog2(DEPTH)'(dut.ports[0].endpoint.r_held_q.slot)].retired)
      else $fatal(1,"stale read retirement changed live owner");
    rready[0]=1;wait_drained();

    // A healthy boot change must not reset slot generations while an old
    // retirement is still in transport. Force only the consumer's READY;
    // actual B feedback and the core's retirement FIFO handshake still occur.
    force dut.ports[0].endpoint.retire_local_ready=0;
    baseline=int'(source_b);
    send_aw(0,2500,0,4);send_w(0,4);
    while(int'(source_b)==baseline||!core_settled)@(negedge core_clk);
    assert(dut.ports[0].endpoint.write_count>0)else $fatal(1,"delayed retirement was not exercised");
    enable=0;while(admission_busy)@(negedge core_clk);
    cfg.boot_epoch++;cfg.config_epoch++;
    while(!rearm_ready)@(negedge core_clk);
    rearm=1;@(negedge core_clk);rearm=0;
    repeat(20)@(negedge axi_clk0);
    assert(!initialized&&!fault&&dut.ports[0].endpoint.config_q.boot_epoch!=cfg.boot_epoch
        &&dut.ports[0].endpoint.write_count>0)
      else $fatal(1,"healthy boot change init=%b fault=%b boot=%0d/%0d count=%0d valid=%b ready=%b sourcefault=%b drained=%b",
        initialized,fault,dut.ports[0].endpoint.config_q.boot_epoch,cfg.boot_epoch,
        dut.ports[0].endpoint.write_count,dut.ports[0].endpoint.retire_local_valid,
        dut.ports[0].endpoint.retire_local_ready,dut.ports[0].endpoint.fault_q,dut.ports[0].endpoint.revoke_drained);
    // Releasing a forced variable retains its last value until its procedural
    // driver next executes. Restore the legitimate constant READY first; the
    // simulator may optimize that constant driver to startup evaluation only.
    force dut.ports[0].endpoint.retire_local_ready=1;
    @(negedge axi_clk0);release dut.ports[0].endpoint.retire_local_ready;
    #0.001;assert(dut.ports[0].endpoint.retire_local_ready)
      else $fatal(1,"retirement consumer READY did not restore");
    while(!initialized)@(negedge core_clk);
    source_seq[0]=0;source_seq[1]=0;enable=1;
    send_ar(0,2501,0,4);wait_drained();
    assert(dut.ports[0].endpoint.write_count==0&&!fault)
      else $fatal(1,"old retirement leaked into new generation");

    // The elastic R register may replace a consumed final with another
    // transaction, but a duplicate of the consumed identity must fault. Queue
    // the duplicate through the real core response FIFO before releasing R.
    rready[0]=0;send_ar(0,2510,0,1);
    while(!rvalid[0])@(negedge axi_clk0);
    duplicate_read=r[0];duplicate_slot=dut.ports[0].endpoint.r_held_q.slot;
    duplicate_generation=dut.ports[0].endpoint.r_held_q.generation;
    @(negedge core_clk);
    force cr[0]=duplicate_read;force crslot[0]=duplicate_slot;
    force crgen[0]=duplicate_generation;force crvalid[0]=1;
    do @(posedge core_clk);while(!crready[0]);
    @(negedge core_clk);
    force crvalid[0]=0;
    release cr[0];release crslot[0];release crgen[0];release crvalid[0];
    while(!dut.ports[0].endpoint.r_local_valid)@(negedge axi_clk0);
    assert(dut.ports[0].endpoint.r_local.axi==duplicate_read)
      else $fatal(1,"duplicate final was not queued through actual FIFO");
    baseline=int'(source_r);@(negedge axi_clk0);rready[0]=1;
    @(posedge axi_clk0);@(negedge axi_clk0);
    assert(!rvalid[0]&&dut.ports[0].endpoint.fault_q&&int'(source_r)==baseline+1)
      else $fatal(1,"same-edge duplicate final R escaped identity fence");
    while(!fault)@(negedge core_clk);
    recover(1);

    // B was consumed, but its actual exposure/handshake feedback cannot leave
    // the event transport yet. The surviving exposed ledger owns late error.
    assert(late_error==0)else $fatal(1,"unexpected late error before fault");
    eventready[0]=0;baseline=int'(source_b);
    send_aw(0,1500,7168,4);send_w(0,4);
    while(int'(source_b)==baseline)@(negedge axi_clk0);
    assert(!bvalid[0])else $fatal(1,"late-error test did not consume B");
    fatal=1;repeat(20)@(negedge axi_clk0);
    assert(late_error[0])else $fatal(1,"consumed B lost late error while feedback crossed");
    while(!settled)@(negedge core_clk);
    eventready=3;recover(1);

    rready[0]=0;bready[0]=0;
    send_ar(0,1000,1024,4);
    while(!rvalid[0])@(negedge axi_clk[0]);held_read=r[0];
    send_aw(0,1001,2048,4);send_w(0,4,0);
    while(!bvalid[0])@(negedge axi_clk[0]);held_b=b[0];
    fatal=1;repeat(20)@(negedge axi_clk[0]);
    assert(!abort_settled&&!settled)else $fatal(1,"abort settled before actual held B/R handshakes");
    assert(rvalid[0]&&r[0]==held_read&&bvalid[0]&&b[0]==held_b&&late_error[0])else $fatal(1,"fault retracted exposed responses");
    rready[0]=1;bready[0]=1;
    while(!settled)@(negedge core_clk);
    recover(0);repeat(40)@(negedge core_clk);
    assert(!initialized&&!awready[0]&&!arready[0])else $fatal(1,"stale boot epoch resumed traffic");
    recover(1);stage=4;

    automatic_responses=0;
    send_aw(0,1100,3072,4);
    send_ar(0,1101,4096,4);
    // No W yet. Core reset must preserve both local accepted descriptors.
    enable=0;aborted_imports+=int'(actual_order.size());actual_order.delete();core_rst_n=0;repeat(10)@(negedge axi_clk[0]);
    send_w(0,4);rready=3;bready=3;
    while(expected_w0.size()!=0||expected_r0.size()!=0)@(negedge axi_clk0);
    assert(source_writes==source_b)else $fatal(1,"missing B after core reset");
    automatic_responses=1;recover(1);stage=5;
    // A peer AXI reset aborts the transport while preserving this port ledger.
    automatic_responses=0;send_ar(0,1200,5120,4);
    aborted_imports+=int'(actual_order.size());actual_order.delete();
    axi_rst_n[1]=0;repeat(8)@(negedge axi_clk[0]);axi_rst_n[1]=1;
    while(!settled)@(negedge axi_clk[0]);
    automatic_responses=1;recover(1);stage=6;
    send_ar(0,1300,6144,4);wait_drained();
    assert(actual_order.size()==0)else $fatal(1,"accepted source address never imported or explicitly aborted");
    assert(source_writes+source_reads==imports+aborted_imports)else $fatal(1,"address acceptance accounting");
    $display("AXI_BRIDGE_PASS bits=%0d depth=%0d imports=%0d AW=%0d AR=%0d B=%0d Rbeats=%0d exposures=%0d finalhandshakes=%0d",
      DATA_BITS,DEPTH,imports,source_writes,source_reads,source_b,source_r,model_exposures,model_handshakes);
    $display("AXI_STATS first_accept=%0.3f last_accept=%0.3f accepted_current_epoch=%0d grant_CK=%0d late=%b",first_accept,last_accept,accepted_addresses,grant_cycles,late_error);
    $finish;
  end
  initial begin #200000;$fatal(1,"AXI timeout stage=%0d initialized=%b fault=%b settled=%b core_settled=%b state=%0d busy=%b recovery=%b/%b/%b/%b fresh=%b local=%b remote=%b request=%b/%b inflight=%b cfg=%b topaw=%b topar=%b srccount=%0d/%0d",stage,initialized,fault,settled,core_settled,dut.state_q,admission_busy,dut.ports[0].endpoint.recovery_request_q,dut.ports[0].endpoint.recovery_sync_q,dut.ports[0].endpoint.recovery_seen_q,dut.ports[0].endpoint.recovery_ack_q,dut.ports[0].endpoint.fresh_epoch,dut.ports[0].endpoint.local_faults,dut.ports[0].endpoint.remote_faults,rearm_ready,rearm,dut.ports[0].endpoint.recovery_inflight_q,configuration_valid(cfg,2,1,ADDRESS_BITS),awvalid,arvalid,source_writes,source_reads);end
endmodule
/* verilator lint_on BLKSEQ */
