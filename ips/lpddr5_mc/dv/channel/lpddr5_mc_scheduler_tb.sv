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
  logic maintenance_ready,pages_closed,rows_closed,stop_requested,close_requested=0;
  logic [63:0] accepted,issued,replayed,max_wait;
  logic unused_debug;
  assign unused_debug=^{event_value,pair_pending,accepted,issued,replayed,sync_rank,stop_requested,rows_closed,max_wait};
  lpddr5_mc_scheduler #(.QUEUE_DEPTH(16)) dut (
    .clk_ck_i(clk),.rst_ni(rst_n),.abort_i(abort_request),.config_i(cfg),.admission_i(admission),.issue_enable_i(issue_enable),.command_enable_i(1'b1),
    .request_valid_i(request_valid),.request_ready_o(request_ready),.request_i(request),
    .launch_valid_o(launch_valid),.launch_ready_i(launch_ready),.launch_o(launch),.ca_o(ca),.event_o(event_value),
    .sync_o(sync),.sync_write_o(sync_write),.sync_rank_o(sync_rank),.wck_disabled_i(wck_disabled),
    .wck_reusable_i(wck_reusable),.wck_rank_i(wck_rank),.wck_write_i(wck_write),
    .drained_o(drained),.pair_pending_o(pair_pending),.fault_o(fault),.maintenance_valid_i(maintenance_valid),
    .maintenance_all_i(1'b1),.maintenance_pair_i(3'd0),.activate_allowed_i('1),.close_i(close_requested),.stop_requested_o(stop_requested),.maintenance_rfm_i(maintenance_rfm),.maintenance_rank_i(maintenance_rank),.maintenance_ready_o(maintenance_ready),
    .pages_closed_o(pages_closed),.rows_closed_o(rows_closed),.accepted_o(accepted),.issued_o(issued),.replayed_o(replayed),.max_wait_o(max_wait));
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
    cfg.service_div=3'(divisor_value); cfg.age_tick_div=8; cfg.read_urgent_age=2;cfg.write_urgent_age=2;
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
        if (cap_on==1 && borrowing!=0) assert(budget<=2*cap_value) else $fatal(1,"borrowed combined byte cap CK=%0d bytes=%0d",cycle,budget);
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
    cfg.service_div=3'(divisor_value);cfg.age_tick_div=2;cfg.read_urgent_age=4;cfg.write_urgent_age=4;
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
  task automatic direction_age_case;
    int done_count;
    rst_n=0;request_valid=0;issue_enable=0;cfg='0;request='0;tick();rst_n=1;
    cfg.profile=fixture_profile(0,0,0,1,0,0);cfg.profile.ranks=1;
    cfg.service_div=1;cfg.age_tick_div=8;cfg.read_urgent_age=16'hffff;cfg.write_urgent_age=0;
    cfg.read_weight=16'hffff;cfg.write_weight=16'hffff;cfg.bw_window=3000;
    maintenance_valid=0;wck_disabled=1;wck_reusable=0;launch_ready=1;
    for(int n=0;n<2;n++) begin
      request='0;request.tag=64'(n)+1;request.generation=1;request.is_write=1'(n);
      request.is_64_bytes=1;request.byte_enable='1;request.column=12'(n*2);
      request.qos_class=n==0 ? 3 : 0;
      request_valid=1;#1;assert(request_ready) else $fatal(1,"direction age admission");tick();
    end
    request_valid=0;issue_enable=1;done_count=0;
    for(int cycle=0;cycle<1000;cycle++) begin
      #1;
      if(launch_valid) begin
        if(done_count==0) assert(launch.is_write) else $fatal(1,"write age threshold ignored in favor of read QoS/direction");
        done_count++;
      end
      tick();assert(!fault) else $fatal(1,"direction age fault");
      if(done_count==2) break;
    end
    assert(done_count==2 && drained) else $fatal(1,"direction age progress");
  endtask
  task automatic turnaround_case(input int minimum_ck);
    int read_ck,write_ck;
    rst_n=0;request_valid=0;issue_enable=0;cfg='0;request='0;tick();rst_n=1;
    cfg.profile=fixture_profile(0,0,0,1,0,0);cfg.profile.ranks=1;
    cfg.read_to_write_min_ck=16'(minimum_ck);cfg.service_div=1;
    cfg.age_tick_div=8;cfg.read_urgent_age=32;cfg.write_urgent_age=32;cfg.read_weight=1;cfg.write_weight=1;
    cfg.bw_window=3000;maintenance_valid=0;wck_disabled=1;wck_reusable=0;launch_ready=1;
    for(int n=0;n<2;n++) begin
      request='0;request.tag=64'(n)+64'd1;request.generation=1;request.is_write=1'(n);
      request.is_64_bytes=1;request.byte_enable='1;request.column=12'(n*2);
      request_valid=1;#1;assert(request_ready) else $fatal(1,"turnaround admission");tick();
    end
    request_valid=0;issue_enable=1;read_ck=-1;write_ck=-1;
    for(int cycle=0;cycle<1000;cycle++) begin
      #1;
      if(launch_valid) begin
        if(launch.is_write) begin
          assert(read_ck>=0 && cycle-read_ck>=minimum_ck) else $fatal(1,"programmed read/write turnaround bypassed");
          write_ck=cycle;
        end else read_ck=cycle;
      end
      tick();assert(!fault) else $fatal(1,"turnaround fault");
      if(write_ck>=0) break;
    end
    assert(read_ck>=0 && write_ck>read_ck && drained) else $fatal(1,"turnaround progress");
    if(minimum_ck==0) assert(write_ck-read_ck<256) else $fatal(1,"zero minimum blocked derived safe turnaround");
  endtask
  task automatic policy_setup;
    rst_n=0;request_valid=0;issue_enable=0;cfg='0;request='0;tick();rst_n=1;
    cfg.profile=fixture_profile(0,0,0,1,0,0);cfg.profile.ranks=1;
    cfg.service_div=1;cfg.age_tick_div=8;cfg.read_urgent_age=16'hffff;cfg.write_urgent_age=16'hffff;
    cfg.read_weight=16'hffff;cfg.write_weight=16'hffff;cfg.bw_window=3000;
    maintenance_valid=0;wck_disabled=1;wck_reusable=0;launch_ready=1;
  endtask
  task automatic policy_admit(input int tag_value,column_value,row_value,qos_value,input bit write_value);
    assert(column_value>=0 && column_value<64 && row_value>=0 && row_value<32768 && qos_value>=0 && qos_value<4)
      else $fatal(1,"policy input bounds");
    request='0;request.tag=64'(tag_value);request.generation=1;request.is_write=write_value;
    request.is_64_bytes=1;request.byte_enable='1;request.column=12'(column_value);request.row=18'(row_value);request.qos_class=2'(qos_value);
    request_valid=1;#1;assert(request_ready) else $fatal(1,"policy admission");tick();request_valid=0;
  endtask
  task automatic batch_policy_case(input int high_value,low_value,read_min,expected_reads,expected_writes);
    int reads_before_write,writes_before_read,done_count;
    bit saw_write,saw_read_again;
    policy_setup();cfg.write_high=7'(high_value);cfg.write_low=7'(low_value);
    cfg.read_batch_min=8'(read_min);cfg.write_batch_min=2;cfg.write_batch_max=4;
    for(int n=0;n<8;n++) policy_admit(n+1,n*2,0,0,0);
    for(int n=0;n<8;n++) policy_admit(n+9,n*2+16,0,0,1);
    reads_before_write=0;writes_before_read=0;done_count=0;saw_write=0;saw_read_again=0;issue_enable=1;
    for(int cycle=0;cycle<3000;cycle++) begin
      #1;
      if(launch_valid) begin
        if(launch.is_write) begin saw_write=1;if(!saw_read_again) writes_before_read++;end
        else if(!saw_write) reads_before_write++;
        else saw_read_again=1;
        done_count++;
      end
      tick();assert(!fault) else $fatal(1,"batch policy fault");
      if(done_count==16) break;
    end
    assert(done_count==16 && drained && reads_before_write==expected_reads && writes_before_read==expected_writes)
      else $fatal(1,"watermark/batch output order high%0d low%0d min%0d reads%0d writes%0d completed%0d",high_value,low_value,read_min,reads_before_write,writes_before_read,done_count);
  endtask
  task automatic idle_policy_case(input int idle_value);
    int access_ck,pre_ck;
    policy_setup();cfg.page_idle_ck=32'(idle_value);policy_admit(1,0,0,0,0);issue_enable=1;access_ck=-1;pre_ck=-1;
    for(int cycle=0;cycle<250;cycle++) begin
      #1;
      if(launch_valid) access_ck=cycle;
      if(ca.cs!=0 && ca.address[6:0]==120) begin pre_ck=cycle;break;end
      tick();assert(!fault) else $fatal(1,"idle page fault");
    end
    assert(access_ck>=0) else $fatal(1,"idle page transfer missing");
    if(idle_value==0) assert(pre_ck<0 && !rows_closed) else $fatal(1,"disabled page idle closed row");
    else assert(pre_ck>=access_ck+idle_value && pre_ck<access_ck+idle_value+4) else $fatal(1,"page idle timeout bypassed or failed");
  endtask
  task automatic hit_policy_case(input int cap_value);
    int done_count,miss_position;
    policy_setup();cfg.hit_cap=16'(cap_value);policy_admit(1,0,0,3,0);issue_enable=1;
    for(int cycle=0;cycle<200;cycle++) begin
      #1;if(launch_valid) begin tick();break;end
      tick();
    end
    issue_enable=0;repeat(80) tick();
    for(int n=0;n<4;n++) policy_admit(n+2,n*2+2,0,3,0);
    policy_admit(6,0,1,0,0);issue_enable=1;done_count=0;miss_position=-1;
    for(int cycle=0;cycle<1000;cycle++) begin
      #1;
      if(launch_valid) begin
        if(launch.tag==6) miss_position=done_count;
        done_count++;
      end
      tick();assert(!fault) else $fatal(1,"hit cap fault");
      if(done_count==5) break;
    end
    assert(done_count==5 && drained) else $fatal(1,"hit cap progress");
    if(cap_value==0) assert(miss_position==4) else $fatal(1,"uncapped QoS/row preference changed");
    else assert(miss_position<=cap_value) else $fatal(1,"hit cap failed to serve conflicting low-QoS row");
  endtask
  task automatic borrowed_arrival_case;
    int done_count,bytes_used,last_window,window_now;
    bit write_added;
    policy_setup();cfg.caps_enable=1;cfg.work_conserving=1;
    cfg.bw_window=512;cfg.read_cap=64;cfg.write_cap=64;
    for(int n=0;n<4;n++) policy_admit(n+1,n*2,0,0,0);
    done_count=0;bytes_used=0;last_window=0;write_added=0;issue_enable=1;
    for(int cycle=0;cycle<2200;cycle++) begin
      #1;window_now=int'(dut.ck_q)/512;
      if(window_now!=last_window) begin last_window=window_now;bytes_used=0;end
      if(launch_valid) begin
        done_count++;bytes_used+=64;
        assert(bytes_used<=128) else $fatal(1,"new opposite traffic overspent borrowed window");
      end
      tick();
      if(done_count==2 && !write_added) begin
        // The first two reads consumed both direction budgets. A write
        // arriving now must wait, despite its own unused per-direction cap.
        issue_enable=0;policy_admit(5,16,0,0,1);issue_enable=1;write_added=1;
      end
      assert(!fault) else $fatal(1,"borrowed arrival fault");
      if(done_count==5) break;
    end
    assert(write_added && done_count==5 && drained) else $fatal(1,"borrowed arrival progress");
  endtask
  task automatic zero_weight_case;
    int completed;
    policy_setup();cfg.read_weight=0;cfg.read_urgent_age=4;policy_admit(1,0,0,0,0);issue_enable=1;completed=0;
    for(int cycle=0;cycle<200;cycle++) begin
      #1;
      if(launch_valid) begin assert(cycle>=30) else $fatal(1,"zero weighted share issued before urgency");completed++;end
      tick();if(completed!=0) break;
    end
    assert(completed==1 && drained && !fault) else $fatal(1,"zero weighted share lost urgent escape");
  endtask
  // After rank closure, consecutive misses in one BG can consume the first
  // ACT slots even though another group could overlap its shorter tCCD.
  // Check actual startup payload cadence at every CK/4 selection phase.
  task automatic group_startup_case(input int phase_offset);
    int completed,first_ck,second_ck;
    logic [2:0] seen;
    bit sync_now;
    policy_setup();
    cfg.service_div=4;wck_write=1;
    for(int n=0;n<3;n++) begin
      request='0;request.tag=64'(n)+1;request.generation=1;request.is_write=1;
      request.is_64_bytes=1;request.byte_enable='1;request.row=7;
      // Older bank2/bank3 misses share a BG; bank4 can run in parallel.
      request.bank_group=2'((n+2)/4);request.bank=2'((n+2)%4);
      request_valid=1;#1;assert(request_ready) else $fatal(1,"BG startup admission");tick();
    end
    request_valid=0;repeat(phase_offset) tick();issue_enable=1;
    completed=0;first_ck=-1;second_ck=-1;seen=0;
    for(int cycle=0;cycle<200;cycle++) begin
      #1;
      sync_now=sync;
      if(launch_valid) begin
        assert(launch.tag>=1 && launch.tag<=3 && !seen[int'(launch.tag)-1]) else $fatal(1,"BG startup identity");
        seen[int'(launch.tag)-1]=1;
        if(completed==0) first_ck=cycle;
        if(completed==1) second_ck=cycle;
        completed++;
      end
      tick();
      if(sync_now) begin wck_disabled=0;wck_reusable=1;end
      assert(!fault) else $fatal(1,"BG startup fault");
      if(completed==3) break;
    end
    assert(completed==3 && seen=='1 && drained) else $fatal(1,"BG startup progress");
    assert(second_ck-first_ck<=8) else $fatal(1,"BG startup missed CK/4 slot phase=%0d gap=%0d",phase_offset,second_ck-first_ck);
    $display("BG_STARTUP_PASS phase=%0d gap=%0d",phase_offset,second_ck-first_ck);
  endtask
  // A rank-wide PRE also targets a closed bank still recovering from PREpb.
  // Produce the first PRE through ordinary idle policy, then request closure
  // while another bank is open. Check emitted commands rather than timer RAM.
  task automatic precharge_recovery_case(input int mode_value);
    int completed,single_ck,all_ck,refresh_ck;
    policy_setup();cfg.page_idle_ck=100;
    for(int bank_value=0;bank_value<2;bank_value++) begin
      request='0;request.tag=64'(bank_value)+64'd1;request.generation=1;
      request.is_64_bytes=1;request.byte_enable='1;request.bank=2'(bank_value);
      request_valid=1;#1;assert(request_ready) else $fatal(1,"precharge recovery admission");tick();
    end
    request_valid=0;issue_enable=1;completed=0;single_ck=-1;all_ck=-1;refresh_ck=-1;
    for(int cycle=0;cycle<400;cycle++) begin
      bit single_now;
      single_now=0;#1;
      if(launch_valid) completed++;
      if(ca.cs!=0 && ca.address[6:0]==120) begin
        if(!ca.address[13]) begin
          assert(single_ck<0 && completed==2) else $fatal(1,"precharge recovery stimulus");
          single_ck=cycle;single_now=1;
        end else begin
          assert(single_ck>=0 && all_ck<0) else $fatal(1,"unexpected rank-wide precharge");
          assert(cycle-single_ck>=int'(cfg.profile.dram_ck_trp_pb))
            else $fatal(1,"PREab overlapped closed-bank PREpb recovery mode=%0d gap=%0d",mode_value,cycle-single_ck);
          all_ck=cycle;
        end
      end
      if(maintenance_ready) begin
        assert(all_ck>=0 && cycle-all_ck>=int'(cfg.profile.dram_ck_trp_ab))
          else $fatal(1,"maintenance before PREab recovery");
        refresh_ck=cycle;
      end
      tick();assert(!fault) else $fatal(1,"precharge recovery fault");
      if(single_now) begin
        cfg.page_idle_ck=0;issue_enable=0;wck_disabled=1;wck_reusable=0;
        maintenance_valid=mode_value!=2;maintenance_rfm=mode_value==1;close_requested=mode_value==2;
      end
      if(refresh_ck>=0 || (mode_value==2 && all_ck>=0 && pages_closed)) break;
    end
    assert(single_ck>=0 && all_ck>=single_ck+int'(cfg.profile.dram_ck_trp_pb)
      && all_ck<=single_ck+int'(cfg.profile.dram_ck_trp_pb)+1
      && (mode_value==2 || refresh_ck>=0)) else $fatal(1,"precharge recovery failed progress");
    maintenance_valid=0;maintenance_rfm=0;close_requested=0;issue_enable=1;
    $display("PRECHARGE_RECOVERY_PASS mode=%0d gap=%0d",mode_value,all_ck-single_ck);
  endtask
  initial begin
    cfg='0; request='0;
    for(int phase_offset=0;phase_offset<4;phase_offset++) group_startup_case(phase_offset);
    for(int mode_value=0;mode_value<3;mode_value++) precharge_recovery_case(mode_value);
    for (int div=1;div<=4;div*=2) begin
      run_case(div,0,0,0,0); run_case(div,1,0,0,0);
      mixed_case(div,0);mixed_case(div,1);
      run_case(div,0,1,64,0); run_case(div,0,1,0,0); run_case(div,0,1,0,1); run_case(div,0,1,64,1);
    end
    batch_policy_case(4,1,2,2,4);batch_policy_case(4,7,2,2,2);
    batch_policy_case(4,1,3,3,4);batch_policy_case(0,0,2,8,8);
    idle_policy_case(0);idle_policy_case(100);
    hit_policy_case(0);hit_policy_case(2);zero_weight_case();borrowed_arrival_case();
    direction_age_case();
    turnaround_case(0);turnaround_case(256);
    $display("PASS scheduler: CK/2/4 pairs, mixed directions/classes/ports/ranks, credit-stall replay/progress, finite queues, row changes, strict/zero/borrowed caps and maintenance with selection off");
    $finish;
  end
endmodule
