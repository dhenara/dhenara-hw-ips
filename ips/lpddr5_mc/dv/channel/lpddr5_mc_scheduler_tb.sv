module lpddr5_mc_scheduler_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0,rst_n=0,abort_request=0,admission=1,issue_enable=1;
  lpddr5_mc_channel_config_t cfg;
  logic request_valid=0,request_ready,launch_valid,launch_ready=1;
  lpddr5_mc_granule_t request,launch;
  lpddr5_mc_ca_t ca;
  lpddr5_mc_event_t event_value;
  logic sync,sync_write,sync_rank,wck_disabled=1,wck_reusable=0,wck_rank=0,wck_write=0;
  logic drained,pair_pending,fault,maintenance_valid=0,maintenance_rfm=0,maintenance_rank=0;
  logic maintenance_ready,pages_closed,rows_closed,stop_requested;
  logic [63:0] accepted,issued,replayed;
  logic unused_debug;
  assign unused_debug=^{event_value,pair_pending,accepted,issued,replayed,sync_rank,stop_requested,rows_closed};
  lpddr5_mc_scheduler #(.QUEUE_DEPTH(16)) dut (
    .clk_ck_i(clk),.rst_ni(rst_n),.abort_i(abort_request),.config_i(cfg),.admission_i(admission),.issue_enable_i(issue_enable),.command_enable_i(1'b1),
    .request_valid_i(request_valid),.request_ready_o(request_ready),.request_i(request),
    .launch_valid_o(launch_valid),.launch_ready_i(launch_ready),.launch_o(launch),.ca_o(ca),.event_o(event_value),
    .sync_o(sync),.sync_write_o(sync_write),.sync_rank_o(sync_rank),.wck_disabled_i(wck_disabled),
    .wck_reusable_i(wck_reusable),.wck_rank_i(wck_rank),.wck_write_i(wck_write),
    .drained_o(drained),.pair_pending_o(pair_pending),.fault_o(fault),.maintenance_valid_i(maintenance_valid),
    .maintenance_all_i(1'b1),.maintenance_pair_i(3'd0),.activate_allowed_i('1),.close_i(1'b0),.stop_requested_o(stop_requested),.maintenance_rfm_i(maintenance_rfm),.maintenance_rank_i(maintenance_rank),.maintenance_ready_o(maintenance_ready),
    .pages_closed_o(pages_closed),.rows_closed_o(rows_closed),.accepted_o(accepted),.issued_o(issued),.replayed_o(replayed));
  task automatic tick;
    #1; clk=1; #1; clk=0; #1;
  endtask
  task automatic run_case(input int divisor_value,write_access,cap_on,cap_value,borrowing);
    int admitted_count,issued_count,budget,window_number,new_window,act_count;
    logic [3:0] bank_value;
    int last_act[16];
    logic [17:0] open_row[16];
    logic [15:0] open_banks;
    logic [39:0] seen;
    logic [32767:0] pin_used;
    logic pending_act,pending_sync,was_sync;
    logic [17:0] act_row;
    logic [3:0] act_bank;
    rst_n=0; cfg='0; request='0; tick(); rst_n=1;
    cfg.profile=fixture_profile(0,0,0,1,0,0); cfg.profile.ranks=1;
    cfg.service_div=3'(divisor_value); cfg.age_tick_div=8; cfg.urgent_age=2;
    cfg.bw_window=64; cfg.caps_enable=1'(cap_on); cfg.read_cap=32'(cap_value); cfg.write_cap=32'(cap_value);
    cfg.work_conserving=1'(borrowing); cfg.read_weight=1; cfg.write_weight=1;
    admitted_count=0; issued_count=0; budget=0; window_number=0; act_count=0;
    seen=0; pin_used=0; pending_act=0; pending_sync=0; open_banks=0;
    wck_disabled=1; wck_reusable=0; wck_write=1'(write_access);
    maintenance_valid=0;
    for (int b=0;b<16;b++) begin last_act[b]=-1000; open_row[b]=0; end
    for (int cycle=0;cycle<3100;cycle++) begin
      request_valid=admitted_count<40;
      request='0; request.tag=64'(admitted_count)+1; request.generation=1;
      request.is_write=1'(write_access); request.is_64_bytes=1; request.byte_enable='1;
      request.row=18'(admitted_count/16); request.bank_group=2'(admitted_count%4); request.bank=2'((admitted_count/4)%4);
      request.column=12'((admitted_count%32)*2); request.address=40'(admitted_count)*64; request.qos_class=2'(admitted_count%4);
      #1;
      if (request_valid && request_ready) admitted_count++;
      new_window=cycle/64;
      if (new_window!=window_number) begin window_number=new_window; budget=0; end
      if (pending_act) begin
        assert(ca.cs==1 && ca.address[2:0]==3) else $fatal(1,"ACT pair broken div=%0d CK=%0d",divisor_value,cycle);
        act_row[10:0]={ca.address[6:3],ca.address[13:7]};
        open_banks[act_bank]=1; open_row[act_bank]=act_row; last_act[act_bank]=cycle;
        pending_act=0;
      end else if (ca.cs!=0 && ca.address[2:0]==7) begin
        act_bank=ca.address[10:7]; act_row={ca.address[6:3],ca.address[13:11],11'b0};
        pending_act=1; act_count++;
      end
      if (pending_sync) assert(launch_valid) else $fatal(1,"sync pair broken");
      pending_sync=sync;
      was_sync=sync;
      if (sync) assert(sync_write==1'(write_access)) else $fatal(1,"sync direction");
      if (launch_valid) begin
        assert(launch.address==(40'(launch.tag)-40'd1)*40'd64 && launch.data==0 && launch.byte_enable=='1
          && launch.generation==1 && launch.is_write==1'(write_access) && launch.is_64_bytes && launch.channel_index==0
          && launch.rank_index==0 && launch.axi_id==0 && launch.port_index==0 && launch.column==12'(((int'(launch.tag)-1)%32)*2)
          && launch.qos_class==2'(int'(launch.tag)-1)) else $fatal(1,"payload preservation");
        assert(launch.tag>=1 && launch.tag<=40) else $fatal(1,"tag range");
        assert(!seen[int'(launch.tag)-1]) else $fatal(1,"duplicate issue");
        seen[int'(launch.tag)-1]=1; issued_count++;
        bank_value={launch.bank_group,launch.bank};
        assert(open_banks[bank_value] && open_row[bank_value]==launch.row && cycle-last_act[bank_value]>=15) else $fatal(1,"live page/tRCD");
        assert(ca.address[2:0]==(write_access==1 ? 4 : 5)) else $fatal(1,"access opcode");
        budget+=64;
        if (cap_on==1 && borrowing==0) assert(budget<=cap_value) else $fatal(1,"strict byte cap CK=%0d",cycle);
        for (int beat=0;beat<32;beat++) begin
          int at_pin;
          at_pin=cycle*8+16+(write_access==1 ? 9*8+2 : 17*8+6)+beat+(beat>=16 ? 16 : 0);
          assert(at_pin<32768) else $fatal(1,"test trace bound");
          assert(!pin_used[at_pin]) else $fatal(1,"DQ collision");
          pin_used[at_pin]=1;
        end
      end
      tick();
      if (was_sync) begin wck_disabled=0; wck_reusable=1; end
      assert(!fault) else $fatal(1,"scheduler fault");
      if (issued_count==40) break;
    end
    request_valid=0;
    if (cap_on==0 || cap_value>=64) begin
      assert(issued_count==40 && drained && act_count>=16) else $fatal(1,"progress %0d",issued_count);
    end else assert(issued_count==0) else $fatal(1,"zero cap not disabled");
    // Selection is disabled, but precharge/refresh timing still progresses.
    issue_enable=0; wck_disabled=1; wck_reusable=0; maintenance_valid=1;
    for (int i=0;i<200;i++) begin
      #1;
      if (maintenance_ready) begin
        assert(ca.cs==1 && ca.address[6:0]==56 && ca.address[13]) else $fatal(1,"REFab encoding");
        tick(); break;
      end
      tick();
    end
    maintenance_valid=0;
    repeat(170) tick();
    assert(pages_closed) else $fatal(1,"maintenance did not close pages and finish recovery"); issue_enable=1;
  endtask
  // Queue both directions before release. Physical readiness deliberately
  // withdraws access opportunities, so stale picks must replay without loss.
  task automatic mixed_case(input int divisor_value,cap_on);
    logic [31:0] seen;
    logic [65535:0] pins;
    int done_count,read_bytes,write_bytes,last_window,window_now,idx,at_pin;
    assert(divisor_value inside {1,2,4}) else $fatal(1,"mixed divisor");
    rst_n=0;cfg='0;request='0;request_valid=0;issue_enable=0;launch_ready=0;tick();rst_n=1;
    cfg.profile=fixture_profile(0,0,0,1,0,0);
    cfg.service_div=3'(divisor_value);cfg.age_tick_div=2;cfg.urgent_age=4;
    cfg.bw_window=128;cfg.caps_enable=1'(cap_on);cfg.read_cap=128;cfg.write_cap=128;
    cfg.read_weight=1;cfg.write_weight=3;wck_disabled=1;wck_reusable=0;maintenance_valid=0;
    for(int n=0;n<32;n++) begin
      request='0;request.tag=64'(n)+64'd1;request.generation=16'(n+7);
      request.is_write=1'(n%2);request.is_64_bytes=1'(n%4<2);request.byte_enable='1;
      request.port_index=1'(n/2);request.qos_class=2'(n/4);request.rank_index=1'(n/16);
      request.bank_group=2'(n/4);request.bank=2'(n);request.column=12'(n*2);request.address=40'(n*64);
      request_valid=1;#1;assert(request_ready) else $fatal(1,"mixed queue admission");tick();
    end
    request_valid=0;seen=0;pins=0;done_count=0;read_bytes=0;write_bytes=0;last_window=-1;
    for(int cycle=0;cycle<6000;cycle++) begin
      issue_enable=1;
      // Readiness remains low through many proposals, then periodically stalls.
      // Never revoke an accepted pair's reserved data slot.
      launch_ready=cycle>=100 && (cycle%13!=0 || pair_pending);
      #1;
      window_now=int'(dut.ck_q)/128;
      if(window_now!=last_window) begin last_window=window_now;read_bytes=0;write_bytes=0;end
      if(launch_valid) begin
        idx=int'(launch.tag)-1;
        assert(idx>=0 && idx<32 && !seen[idx]) else $fatal(1,"mixed duplicate/unknown issue");
        assert(launch.generation==16'(idx+7) && launch.is_write==1'(idx%2)
          && launch.port_index==1'(idx/2) && launch.qos_class==2'(idx/4)
          && launch.rank_index==1'(idx/16) && launch.address==40'(idx*64)) else $fatal(1,"mixed identity");
        assert(launch_ready && cycle>=100) else $fatal(1,"access bypassed reserved physical credit");
        seen[idx]=1;done_count++;
        if(launch.is_write) write_bytes+=launch.is_64_bytes ? 64 : 32;
        else read_bytes+=launch.is_64_bytes ? 64 : 32;
        if(cap_on!=0) assert(read_bytes<=128 && write_bytes<=128) else $fatal(1,"mixed direction cap overshoot");
        for(int beat=0;beat<(launch.is_64_bytes ? 32 : 16);beat++) begin
          at_pin=cycle*8+16+(launch.is_write ? 9*8+2 : 17*8+6)+beat+(launch.is_64_bytes && beat>=16 ? 16 : 0);
          assert(at_pin<65536 && !pins[at_pin]) else $fatal(1,"mixed DQ collision");pins[at_pin]=1;
        end
      end
      tick();assert(!fault) else $fatal(1,"mixed scheduler fault");
      if(done_count==32) break;
    end
    assert(seen=='1 && drained && replayed>0 && accepted==32 && issued==32)
      else $fatal(1,"mixed urgent classes/ports/ranks or replay progress %0d",done_count);
    launch_ready=1;issue_enable=1;
  endtask
  initial begin
    cfg='0; request='0;
    for (int div=1;div<=4;div*=2) begin
      run_case(div,0,0,0,0); run_case(div,1,0,0,0);
      mixed_case(div,0);mixed_case(div,1);
      run_case(div,0,1,64,0); run_case(div,0,1,0,0); run_case(div,0,1,0,1); run_case(div,0,1,64,1);
    end
    $display("PASS scheduler: CK/2/4 pairs, mixed directions/classes/ports/ranks, credit-stall replay/progress, finite queues, row changes, strict/zero/borrowed caps and maintenance with selection off");
    $finish;
  end
endmodule
