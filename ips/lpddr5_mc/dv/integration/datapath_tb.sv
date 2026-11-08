module datapath_tb #(
  parameter int PORTS=2,CHANNELS=2,RANKS=1,DATA_BITS=128,DEPTH=16,
  parameter int PROFILE_NUMBER=0,SERVICE_DIV=2,INTEGRITY_MODE=0,
  parameter int ID_BITS=8,ADDRESS_BITS=40,GRANULE_BYTES=64
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_host_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  localparam int BYTES=DATA_BITS/8;
  logic clk=0,rst_n=0,enable=0,fatal=0,recover=0,rearm=0;
  always #0.625 clk<=~clk;
  // Keep the fixture clock carrier at two bits. Verilator5.006 otherwise
  // emits duplicate scalar edge-history names for PORTS=1 and aliased clocks.
  logic [1:0] axi_clk=0;
  logic [PORTS-1:0] axi_rst_n=0,monitor_rst_n;
  for(genvar p=0;p<PORTS;p++) begin : clocks
    always #(1.25+0.5*p) axi_clk[p]<=~axi_clk[p];
    lpddr5_mc_reset_sync monitor_reset(.clk_i(axi_clk[p]),.arst_ni(axi_rst_n[p]),.rst_no(monitor_rst_n[p]));
  end
  logic cfg_valid=0,cfg_ready,rearm_ready,initialized,host_cfg_valid,host_fault,settled,late_error;
  logic [31:0] stale;
  logic [63:0] accepted,grants;
  lpddr5_mc_host_config_t host_cfg;
  logic [PORTS-1:0] awvalid=0,arvalid=0,wvalid=0,awready,arready,wready,bvalid,rvalid,bready='1,rready=0;
  lpddr5_mc_axi_address_t [PORTS-1:0] aw,ar;
  lpddr5_mc_axi_write_t [PORTS-1:0] wd;
  lpddr5_mc_axi_b_t [PORTS-1:0] b;
  lpddr5_mc_axi_read_t [PORTS-1:0] r;
  lpddr5_mc_channel_config_t [CHANNELS-1:0] cfg;
  lpddr5_mc_config_mailbox_t [CHANNELS-1:0] mailbox_value;
  lpddr5_mc_channel_command_t [CHANNELS-1:0] command;
  logic [CHANNELS-1:0] command_valid=0,command_ready,command_rejected,reset_confirmed=0,train_ack=0;
  logic [CHANNELS-1:0][31:0] train_token,prepared_epoch,committed_epoch;
  logic [CHANNELS-1:0] prepared,prepare_error,fsp_ready,fsp_declined,notification_valid;
  logic [CHANNELS-1:0][1:0] target_fsp;
  logic [CHANNELS-1:0][4:0] target_frequency;
  lpddr5_mc_channel_notification_t [CHANNELS-1:0] notifications;
  lpddr5_mc_management_in_t [CHANNELS-1:0] management;
  lpddr5_mc_management_out_t [CHANNELS-1:0] management_out;
  logic [CHANNELS-1:0][1:0] report_valid,report_sr,phy_refresh_done;
  lpddr5_mc_ca_t [CHANNELS-1:0] ca;
  lpddr5_mc_wck4_t [CHANNELS-1:0] wck;
  lpddr5_mc_write_bus4_t [CHANNELS-1:0] writes;
  lpddr5_mc_read_request4_t [CHANNELS-1:0] reads;
  lpddr5_mc_return4_t [CHANNELS-1:0] returns;
  lpddr5_mc_channel_pkg::lpddr5_mc_channel_status_t [CHANNELS-1:0] status_value;
  lpddr5_mc_profile_t profile;
  logic memory_reset=1,finish_observations=0;
  logic [7:0] mr43=0;
  logic [4:0] mr4=9;
  int ce_count=0,ue_count=0;
  byte unsigned expected[longint unsigned];
  int b_count[PORTS],r_count[PORTS],checks=0,stage=0;
  int cap_mode=0,cap_stream,cap_ck=0,cap_granule_bytes=64,cap_mixed=0,zero_cap_direction=-1;
  bit [PORTS-1:0] cap_first_write=0,cap_first_read=0;
  string cap_path;
  int zero_admissions[CHANNELS];
  int fairness_mode=-1,fairness_stream,fairness_rows=0,fairness_age=32,fairness_gap=64;
  int fairness_service=SERVICE_DIV;
  string fairness_path;
  longint unsigned written_bytes=0;
  logic [PORTS-1:0][15:0] last_bid;
  logic [PORTS-1:0][1:0] last_bresponse;
  logic unused_debug;
  assign unused_debug=^{axi_clk,command_rejected,prepare_error,fsp_ready,fsp_declined,prepared_epoch,committed_epoch,notification_valid,
    notifications,management_out,wck,late_error,stale,accepted,grants};
  lpddr5_mc_datapath #(.PORTS(PORTS),.CHANNELS(CHANNELS),.RANKS(RANKS),.DATA_BITS(DATA_BITS),.DEPTH(DEPTH),
    .ID_BITS(ID_BITS),.ADDRESS_BITS(ADDRESS_BITS)) dut (
    .clk_ck_i(clk),.rst_ni(rst_n),.cold_rst_ni(rst_n || (|axi_rst_n)),.enable_i(enable),.fatal_i(fatal),.recover_i(recover),.rearm_i(rearm),
    .host_config_valid_i(cfg_valid),.host_config_ready_o(cfg_ready),.host_config_i(host_cfg),.rearm_ready_o(rearm_ready),
    .initialized_o(initialized),.host_config_valid_o(host_cfg_valid),.host_fault_o(host_fault),.host_settled_o(settled),.late_error_o(late_error),.stale_completions_o(stale),
    .axi_clk_i(axi_clk[PORTS-1:0]),.axi_rst_ni(axi_rst_n),.awvalid_i(awvalid),.arvalid_i(arvalid),.wvalid_i(wvalid),.awready_o(awready),.arready_o(arready),.wready_o(wready),
    .aw_i(aw),.ar_i(ar),.w_i(wd),.bvalid_o(bvalid),.rvalid_o(rvalid),.bready_i(bready),.rready_i(rready),.b_o(b),.r_o(r),
    .channel_config_i(cfg),.mailbox_i(mailbox_value),.command_valid_i(command_valid),.command_i(command),.command_ready_o(command_ready),.command_rejected_o(command_rejected),
    .reset_confirmed_i(reset_confirmed),.train_ack_i(train_ack),.train_token_i(train_token),.prepared_valid_o(prepared),.prepare_error_o(prepare_error),
    .fsp_ready_o(fsp_ready),.fsp_declined_o(fsp_declined),.prepared_epoch_o(prepared_epoch),.committed_epoch_o(committed_epoch),.target_fsp_i(target_fsp),.target_frequency_i(target_frequency),
    .notification_valid_o(notification_valid),.notification_ready_i({CHANNELS{1'b1}}),.notification_o(notifications),.management_i(management),
    .rank_report_valid_i(report_valid),.rank_self_refresh_i(report_sr),.phy_refresh_done_i(phy_refresh_done),.management_o(management_out),
    .ca_o(ca),.wck_o(wck),.write_o(writes),.read_request_o(reads),.return_i(returns),.status_o(status_value),.accepted_addresses_o(accepted),.grant_cycles_o(grants));
  lpddr5_mc_observer #(.PORTS(PORTS),.CHANNELS(CHANNELS),.DATA_BITS(DATA_BITS)) observer (
    .clk_ck_i(clk),.rst_ni(rst_n),.axi_clk_i(axi_clk[PORTS-1:0]),.axi_rst_ni(monitor_rst_n),
    .awvalid_i(awvalid),.arvalid_i(arvalid),.wvalid_i(wvalid),.awready_i(awready),.arready_i(arready),.wready_i(wready),
    .aw_i(aw),.ar_i(ar),.w_i(wd),.bvalid_i(bvalid),.rvalid_i(rvalid),.bready_i(bready),.rready_i(rready),.b_i(b),.r_i(r),
    .ca_i(ca),.wck_i(wck),.write_i(writes),.read_i(reads),.return_i(returns),.finish_i(finish_observations));
  for(genvar c=0;c<CHANNELS;c++) begin : memories
    int mr43_count,mr4_count,zq_count,refresh_count,rfm_count;
    logic unused_counts;
    assign unused_counts=^{mr43_count,mr4_count,zq_count,refresh_count,rfm_count};
    lpddr5_mc_channel_memory memory_model (
      .profile_i(cfg[c].profile),.ca_i(ca[c]),.write_i(writes[c]),.read_i(reads[c]),.return_o(returns[c]),
      .mr43_i(mr43),.mr4_i(mr4),.mr43_count_o(mr43_count),.mr4_count_o(mr4_count),.zq_count_o(zq_count),.refresh_count_o(refresh_count),.rfm_count_o(rfm_count));
    initial forever begin
      @(negedge clk);
      if(memory_reset) begin
        memories[c].memory_model.reset();report_valid[c]=2'b11>>(2-RANKS);report_sr[c]=0;
      end else begin memories[c].memory_model.prepare();memories[c].memory_model.sample();end
    end
  end
  initial begin
    wait(rst_n);
    forever begin
      @(posedge clk);
      for(int c=0;c<CHANNELS;c++) if(notification_valid[c]) begin
        if(notifications[c].events[5]) ce_count++;
        if(notifications[c].events[6]) ue_count++;
      end
    end
  end
  for(genvar p=0;p<PORTS;p++) begin : monitors
    initial forever begin
      @(posedge axi_clk[p]);
      if(monitor_rst_n[p]) begin
      if(bvalid[p] && bready[p]) begin b_count[p]=b_count[p]+1;last_bid[p]=b[p].id;last_bresponse[p]=b[p].response;end
      if(rvalid[p] && rready[p]) r_count[p]=r_count[p]+1;
    end
    end
    assert property(@(posedge axi_clk[p]) disable iff(!monitor_rst_n[p]) bvalid[p]&&!bready[p] |=> bvalid[p]&&$stable(b[p]));
    assert property(@(posedge axi_clk[p]) disable iff(!monitor_rst_n[p]) rvalid[p]&&!rready[p] |=> rvalid[p]&&$stable(r[p]));
  end
  `include "expected_profiles.svh"
  task automatic check(input logic condition,input string message);
    assert(condition) else $fatal(1,"stage%0d: %s",stage,message);checks++;
  endtask
  task automatic source_negative(input int p);check(p>=0 && p<PORTS,"source port");if(p==0) @(negedge axi_clk[0]);else @(negedge axi_clk[PORTS-1]);endtask
  task automatic source_positive(input int p);check(p>=0 && p<PORTS,"source port");if(p==0) @(posedge axi_clk[0]);else @(posedge axi_clk[PORTS-1]);endtask
  function automatic logic [15:0] bus_id(input logic [15:0] id);
    return id & (16'hffff>>(16-ID_BITS));
  endfunction
  function automatic lpddr5_mc_axi_address_t address(input logic [15:0] id,input longint unsigned at,input int beats);
    lpddr5_mc_axi_address_t value;
    assert(at<64'd1<<40 && beats>=1 && beats<=256) else $fatal(1,"source address/length bounds");
    value='0;value.id=bus_id(id);value.address=40'(at);value.len=8'(beats-1);value.size=3'($clog2(BYTES));value.burst=1;
    if(cap_mode!=0 || fairness_mode>=0) value.qos={2'b0,id[1:0]};
    return value;
  endfunction
  task automatic send_aw(input int p,input logic [15:0] id,input longint unsigned at,input int beats,input bit raw_id=0);
    source_negative(p);aw[p]=address(id,at,beats);awvalid[p]=1;
    if(raw_id) aw[p].id=id;
    do source_positive(p);while(!awready[p]);source_negative(p);awvalid[p]=0;
  endtask
  // Observe accepted physical descriptors and actual ACCESS launches. The
  // replay reconstructs live identities and charges bytes at issue CK, rather
  // than trusting the scheduler's counters or configuration-window state.
  for(genvar c=0;c<CHANNELS;c++) begin : cap_observers
    wire accepted_request=dut.channels[c].channel.backend.scheduler.request_valid_i
      && dut.channels[c].channel.backend.scheduler.request_ready_o;
    wire issued_request=dut.channels[c].channel.backend.scheduler.launch_valid_o;
    wire [63:0] scheduler_ck=dut.channels[c].channel.backend.scheduler.ck_q;
    lpddr5_mc_granule_t request_value,launch_value;
    assign request_value=dut.channels[c].channel.backend.scheduler.request_i;
    assign launch_value=dut.channels[c].channel.backend.scheduler.launch_o;
    initial begin
      wait(rst_n);
      forever begin
        @(posedge clk);
        if(cap_stream!=0 && !finish_observations) begin
          check(scheduler_ck==64'(cap_ck),"independent cap CK origin");
          if(accepted_request || issued_request) begin
            check(dut.channels[c].channel.backend.scheduler.config_i.caps_enable
              && dut.channels[c].channel.backend.scheduler.config_i.work_conserving==(cap_mode==2)
              && dut.channels[c].channel.backend.scheduler.config_i.bw_window==512
              && dut.channels[c].channel.backend.scheduler.config_i.read_cap==(zero_cap_direction==0 ? 0 : 64)
              && dut.channels[c].channel.backend.scheduler.config_i.write_cap==(zero_cap_direction==1 ? 0 : 64),
              "observed active byte-cap configuration");
            if(cap_mixed==2)
              check(dut.channels[c].channel.backend.scheduler.config_i.write_weight==4
                && dut.channels[c].channel.backend.scheduler.config_i.read_weight==1
                && dut.channels[c].channel.backend.scheduler.config_i.write_batch_min==2
                && dut.channels[c].channel.backend.scheduler.config_i.write_batch_max==4,
                "observed concurrent cap batching");
          end
          if(accepted_request)
            $fdisplay(cap_stream,"{\"kind\":\"admit\",\"ck\":%0d,\"channel\":%0d,\"tag\":%0d,\"generation\":%0d,\"write\":%0d,\"bytes\":%0d,\"port\":%0d,\"qos\":%0d,\"rank\":%0d,\"bank\":%0d,\"row\":%0d,\"column\":%0d}",
              cap_ck,c,request_value.tag,request_value.generation,request_value.is_write,request_value.is_64_bytes ? 64 : 32,request_value.port_index,request_value.qos_class,
              request_value.rank_index,{request_value.bank_group,request_value.bank},request_value.row,request_value.column);
          if(accepted_request && zero_cap_direction==int'(request_value.is_write)) zero_admissions[c]++;
          if(issued_request && launch_value.is_write) cap_first_write[launch_value.port_index]=1;
          if(issued_request)
            $fdisplay(cap_stream,"{\"kind\":\"issue\",\"ck\":%0d,\"channel\":%0d,\"tag\":%0d,\"generation\":%0d,\"write\":%0d,\"bytes\":%0d,\"port\":%0d,\"qos\":%0d,\"rank\":%0d,\"bank\":%0d,\"row\":%0d,\"column\":%0d}",
              cap_ck,c,launch_value.tag,launch_value.generation,launch_value.is_write,launch_value.is_64_bytes ? 64 : 32,launch_value.port_index,launch_value.qos_class,
              launch_value.rank_index,{launch_value.bank_group,launch_value.bank},launch_value.row,launch_value.column);
        end
      end
    end
  end
  always @(posedge clk or negedge rst_n) if(!rst_n) cap_ck<=0;else cap_ck<=cap_ck+1;
  `include "zero_caps.svh"
  `include "fairness.svh"
  task automatic concurrent_cap_source(input int p,input bit write_direction);
    for(int n=0;n<16;n++) begin
      longint unsigned at;
      at=64'd65536+64'(int'(n*256+p*128+((n/4+p)%2)*64+(cap_granule_bytes==32 ? (n%2)*32 : 0)));
      if(write_direction) begin
        send_aw(p,16'(64+n),at,cap_granule_bytes/BYTES);send_w(p,at,cap_granule_bytes/BYTES);
        // Admit the first conflicting read after the first real write, then
        // place a second row-hit write behind it while PRE is timing-blocked.
        if(n==0) wait(cap_first_read[p]);
        if(n%2==1) wait_b(p,16'(64+n),n+1);
      end else begin
        if(n==0) wait(cap_first_write[p]);
        // The first read conflicts in the write bank. Later reads use another
        // bank so the same run also observes ready opposite work at issue.
        at+=64'd65536+(n==0 ? 0 : 64'd16384);
        send_ar(p,16'(96+n),at,cap_granule_bytes/BYTES);
        if(n==0) cap_first_read[p]=1;
        read_result(p,16'(96+n),at,cap_granule_bytes/BYTES);
      end
    end
  endtask
  task automatic cap_case;
    bit all_drained;
    check(PORTS==2 && CHANNELS==2 && INTEGRITY_MODE==0,"cap workload shape");
    check(cap_granule_bytes>=BYTES,"cap source width fits granule");
    if(cap_mixed==2) begin
      // Separate AW/W/B and AR/R source threads retain both directions while
      // each waits for its actual responses. Read/write addresses are disjoint.
      fork
        concurrent_cap_source(0,0);
        concurrent_cap_source(1,0);
        concurrent_cap_source(0,1);
        concurrent_cap_source(1,1);
      join
      wait_settled();
    end else if(cap_mixed==1) begin
      // Queue both directions before collecting responses. Addresses are
      // disjoint, so concurrency cannot depend on an unresolved write hazard.
      // Each port visits both channels and every mapped QoS class.
      for(int n=0;n<16;n++) begin
        for(int p=0;p<PORTS;p++) begin
          longint unsigned at;
          at=64'd32768+64'(int'((n*2+p)*128+((n/4+p)%2)*64+(cap_granule_bytes==32 ? (n%2)*32 : 0)));
          send_aw(p,16'(64+n),at,cap_granule_bytes/BYTES);send_w(p,at,cap_granule_bytes/BYTES);
          send_ar(p,16'(96+n),at+64'd32768,cap_granule_bytes/BYTES);
        end
        for(int p=0;p<PORTS;p++) begin
          longint unsigned at;
          at=64'd65536+64'(int'((n*2+p)*128+((n/4+p)%2)*64+(cap_granule_bytes==32 ? (n%2)*32 : 0)));
          read_result(p,16'(96+n),at,cap_granule_bytes/BYTES);
          wait_b(p,16'(64+n),n+1);
        end
      end
      wait_settled();
    end else for(int phase_number=0;phase_number<2;phase_number++) begin
      // Phase drain makes opposite-direction eligibility unambiguous from
      // admissions alone during the borrowing workload.
      stage=10+phase_number;
      for(int n=0;n<16;n++) begin
        for(int p=0;p<PORTS;p++) begin
          longint unsigned at;
          logic [15:0] id;
          at=64'd32768+64'(int'((n*2+p)*128+((n/4+p)%2)*64+(cap_granule_bytes==32 ? (n%2)*32 : 0)));id=16'(64+n);
          if(phase_number==0) begin
            send_aw(p,id,at,cap_granule_bytes/BYTES);send_w(p,at,cap_granule_bytes/BYTES);wait_b(p,id,n+1);
          end else begin
            send_ar(p,id,at,cap_granule_bytes/BYTES);read_result(p,id,at,cap_granule_bytes/BYTES);
          end
        end
      end
      wait_settled();
    end
    check(!host_fault && stale==0,"cap traffic health");
    for(int c=0;c<CHANNELS;c++) check(!status_value[c].fault,"cap channel health");
    // Host settlement does not include an independently due MR4 return.
    // Quiesce the real channel owners before terminating either observation.
    @(negedge clk);enable=0;send_command(CH_CMD_QUIESCE);
    all_drained=0;
    for(int n=0;n<3000;n++) begin
      @(negedge clk);all_drained=1;
      for(int c=0;c<CHANNELS;c++) all_drained&=status_value[c].state==1 && status_value[c].drained;
      if(all_drained) break;
    end
    // Maintenance remains enabled while quiesced. End at this shared sampled
    // boundary; waiting on channels separately can admit another MR4 on one.
    check(all_drained,"cap simultaneous physical/maintenance tail drain");
    finish_observations=1;
    $fdisplay(cap_stream,"{\"kind\":\"end\",\"ck\":%0d}",cap_ck);$fclose(cap_stream);cap_stream=0;
    repeat(2) @(negedge clk);
    $display("DATAPATH_CAP_PASS mode=%0d granule=%0d mixed=%0d checks=%0d",cap_mode,cap_granule_bytes,cap_mixed,checks);
    $display("DATAPATH_INTEGRATION_PASS checks=%0d",checks);$finish;
  endtask
  task automatic send_ar(input int p,input logic [15:0] id,input longint unsigned at,input int beats,input bit raw_id=0);
    source_negative(p);ar[p]=address(id,at,beats);arvalid[p]=1;
    if(raw_id) ar[p].id=id;
    do source_positive(p);while(!arready[p]);source_negative(p);arvalid[p]=0;
  endtask
  task automatic send_w(input int p,input longint unsigned at,input int beats,start=0,limit=0,input bit sparse=0,input byte unsigned salt=0,input bit update_expected=1);
    int finish_at;
    lpddr5_mc_axi_write_t value;
    finish_at=limit==0 ? beats : limit;
    source_negative(p);wvalid[p]=1;
    for(int n=start;n<finish_at;n++) begin
      value='0;value.last=n==beats-1;
      for(int j=0;j<BYTES;j++) begin
        value.strobe[j]=!sparse || (n+j)%3!=0;value.data[j*8+:8]=8'((at+64'(n)*64'(BYTES)+64'(j))^((at+64'(n)*64'(BYTES)+64'(j))>>8)^90)^salt;
        if(value.strobe[j] && update_expected) expected[at+64'(n)*64'(BYTES)+64'(j)]=value.data[j*8+:8];
      end
      wd[p]=value;do source_positive(p);while(!wready[p]);written_bytes+=64'($countones(value.strobe));source_negative(p);
    end
    wvalid[p]=0;
  endtask
  task automatic wait_b(input int p,input logic [15:0] id,input int count,input logic [1:0] response=0,input bit raw_id=0);
    while(b_count[p]<count) source_negative(p);
    check(last_bid[p]==(raw_id ? id : bus_id(id)) && last_bresponse[p]==2'(response),"B identity/status");
  endtask
  task automatic read_result(input int p,input logic [15:0] id,input longint unsigned at,input int beats,input logic [1:0] response=0,input bit raw_id=0);
    source_negative(p);rready[p]=1;
    for(int n=0;n<beats;n++) begin
      do source_positive(p);while(!rvalid[p]);
      check(r[p].id==(raw_id ? id : bus_id(id)) && r[p].last==(n==beats-1) && r[p].response==2'(response),"R identity/count/status");
      for(int j=0;j<BYTES;j++) check(r[p].data[j*8+:8]==(response==0 ? (expected.exists(at+64'(n)*64'(BYTES)+64'(j))!=0 ? expected[at+64'(n)*64'(BYTES)+64'(j)] : 8'd0) : 8'd0),"independent byte scoreboard");
      source_negative(p);
    end
    rready[p]=0;
  endtask
  task automatic parameter_boundaries;
    int before_b;
    logic [15:0] maximum_id,invalid_id;
    longint unsigned invalid_address,region_end,last_address;
    // Use the entire configured ID range on an actual physical write/read.
    maximum_id=bus_id(16'hffff);
    before_b=b_count[0];send_aw(0,maximum_id,64'd98304,64/BYTES);send_w(0,64'd98304,64/BYTES);
    wait_b(0,maximum_id,before_b+1);wait_settled();
    send_ar(PORTS-1,maximum_id,64'd98304,64/BYTES);read_result(PORTS-1,maximum_id,64'd98304,64/BYTES);wait_settled();
    // Transfer the last mapped granule of every channel, including rank one
    // in the two-rank image. Keep all arithmetic wide above the 32-bit limit.
    region_end=64'(host_cfg.regions[0].size);
    for(int c=0;c<CHANNELS;c++) begin
      last_address=region_end-(64'(CHANNELS)-64'(c))*64'd64;
      before_b=b_count[0];send_aw(0,maximum_id,last_address,64/BYTES);send_w(0,last_address,64/BYTES);
      wait_b(0,maximum_id,before_b+1);wait_settled();
      send_ar(PORTS-1,maximum_id,last_address,64/BYTES);read_result(PORTS-1,maximum_id,last_address,64/BYTES);wait_settled();
    end
    // A burst spanning the region end also crosses a 4-KiB boundary. Both
    // require complete DECERR settlement before any physical write effect.
    last_address=region_end-64'd64;before_b=b_count[0];
    send_aw(0,maximum_id,last_address,128/BYTES);
    send_w(.p(0),.at(last_address),.beats(128/BYTES),.salt(8'hff),.update_expected(0));
    wait_b(0,maximum_id,before_b+1,3);wait_settled();
    send_ar(PORTS-1,maximum_id,last_address,128/BYTES);read_result(PORTS-1,maximum_id,last_address,128/BYTES,3);wait_settled();
    // The valid prefix must retain the earlier data, not the rejected salt.
    send_ar(0,maximum_id,last_address,64/BYTES);read_result(0,maximum_id,last_address,64/BYTES);wait_settled();
    before_b=b_count[0];send_aw(0,maximum_id,region_end,64/BYTES);
    send_w(.p(0),.at(region_end),.beats(64/BYTES),.update_expected(0));
    wait_b(0,maximum_id,before_b+1,3);wait_settled();
    send_ar(PORTS-1,maximum_id,region_end,64/BYTES);read_result(PORTS-1,maximum_id,region_end,64/BYTES,3);wait_settled();
    $display("DATAPATH_REGION_BOUNDARY_PASS end=%0d channels=%0d ranks=%0d: last granules, rejected cross/end bursts and unchanged valid prefix",region_end,CHANNELS,RANKS);
    if(ID_BITS<16) begin
      invalid_id=16'(1<<ID_BITS);before_b=b_count[0];
      send_aw(0,invalid_id,64'd98432,64/BYTES,1);send_w(0,64'd98432,64/BYTES);
      wait_b(0,invalid_id,before_b+1,3,1);wait_settled();
      send_ar(PORTS-1,invalid_id,64'd98432,64/BYTES,1);read_result(PORTS-1,invalid_id,64'd98432,64/BYTES,3,1);wait_settled();
      for(int n=0;n<64;n++) expected.delete(64'd98432+64'(n));
      send_ar(0,maximum_id,64'd98432,64/BYTES);read_result(0,maximum_id,64'd98432,64/BYTES);wait_settled();
    end
    if(ADDRESS_BITS<40) begin
      invalid_address=64'd1<<ADDRESS_BITS;before_b=b_count[0];
      send_aw(0,maximum_id,invalid_address,64/BYTES);send_w(0,invalid_address,64/BYTES);
      wait_b(0,maximum_id,before_b+1,3);wait_settled();
      send_ar(PORTS-1,maximum_id,invalid_address,64/BYTES);read_result(PORTS-1,maximum_id,invalid_address,64/BYTES,3);wait_settled();
    end
    check(!host_fault && stale==0,"parameter-boundary health");
    $display("DATAPATH_PARAMETER_PASS id_bits=%0d address_bits=%0d granule=%0d: maximum ID, full DECERR settlement and no rejected-write effect",ID_BITS,ADDRESS_BITS,GRANULE_BYTES);
  endtask
  task automatic send_command(input lpddr5_mc_channel_command_t value);
    @(negedge clk);check(&command_ready,"channel command readiness");
    for(int c=0;c<CHANNELS;c++) command[c]=value;
    command_valid='1;@(negedge clk);command_valid=0;
  endtask
  task automatic publish_and_boot;
    for(int n=0;n<300;n++) begin
      @(negedge clk);if(settled) break;
    end
    $display("initial settlement=%0d core=%0d bridge=%0d admission=%0d",settled,dut.host.core_settled,dut.host.bridge_settled,dut.host.admission_busy);
    check(settled,"initial host settlement");
    @(negedge clk);
    for(int c=0;c<CHANNELS;c++) begin
      mailbox_value[c]='0;mailbox_value[c].destinations=2'((1<<CHANNELS)-1);mailbox_value[c].epoch=cfg[c].config_epoch;
      mailbox_value[c].payload_crc=config_crc(cfg[c]);mailbox_value[c].prepare=1;
    end
    @(negedge clk);check(&prepared && !(|prepare_error),"channel prepare");
    for(int c=0;c<CHANNELS;c++) begin mailbox_value[c].prepare=0;mailbox_value[c].commit=1;end
    @(negedge clk);mailbox_value='0;
    $display("channel configurations committed");$fflush();
    while(!cfg_ready) @(negedge clk);
    cfg_valid=1;@(negedge clk);cfg_valid=0;check(host_cfg_valid,"host configuration");$display("host configuration installed");$fflush();
    for(int c=0;c<CHANNELS;c++) begin management[c].init_complete=0;management[c].training_epoch=cfg[c].boot_epoch;end
    repeat(4) @(negedge clk);
    for(int c=0;c<CHANNELS;c++) management[c].init_complete=1;
    for(int n=0;n<50;n++) begin
      @(negedge clk);
      if(status_value[0].train_request) break;
    end
    for(int c=0;c<CHANNELS;c++) check(status_value[c].train_request,$sformatf("training request channel%0d state=%0d valid=%0d fault=%0d reports=%b init=%0d wck=%0d",c,status_value[c].state,status_value[c].config_valid,status_value[c].fault,report_valid[c],management[c].init_complete,status_value[c].pin_drained));
    @(negedge clk);train_ack='1;
    for(int c=0;c<CHANNELS;c++) train_token[c]=cfg[c].boot_epoch;
    @(negedge clk);train_ack=0;
    for(int c=0;c<CHANNELS;c++) begin
      for(int n=0;n<100;n++) begin
        @(negedge clk);if(status_value[c].state==1 && status_value[c].memory_trusted) break;
      end
      check(status_value[c].state==1 && status_value[c].memory_trusted,$sformatf("boot channel%0d state=%0d fault=%0d training=%0d/%0d",c,status_value[c].state,status_value[c].fault,status_value[c].train_request,status_value[c].train_done));
    end
    $display("channels trained");$fflush();
    for(int n=0;n<100;n++) begin @(negedge clk);if(rearm_ready) break;end
    check(rearm_ready,$sformatf("host rearm readiness settled=%0d cfg=%0d admission=%0d transports=%b",settled,host_cfg_valid,dut.host.admission_busy,dut.host.bridge.endpoint_transport_reset_ready));
    rearm=1;@(negedge clk);rearm=0;
    for(int n=0;n<200;n++) begin @(negedge clk);if(initialized) break;end
    check(initialized,$sformatf("host initialization readiness endpoint=%b fault=%b",dut.host.bridge.endpoint_initialized,dut.host.bridge.endpoint_fault));
    $display("datapath boot complete");$fflush();
    send_command(CH_CMD_ENABLE);@(negedge clk);enable=1;
  endtask
  task automatic wait_settled;
    for(int n=0;n<3000;n++) begin @(negedge clk);if(settled) return;end
    $fatal(1,"stage%0d accepted host work failed to drain; requests=%b ready=%b hostfault=%0d",stage,dut.request_valid,dut.request_ready,host_fault);
  endtask
  task automatic inline_snapshot(input string phase_name);
    logic [511:0] data_value;
    logic [63:0] check_value;
    data_value=0;check_value=0;
    for(int j=0;j<64;j++) data_value[j*8+:8]=memories[0].memory_model.memory_bytes[64'(j)];
    for(int j=0;j<8;j++) check_value[j*8+:8]=memories[0].memory_model.memory_bytes[64'((1073741824/576)*512)+64'(j)];
    $display("INLINE_SNAPSHOT {\"phase\":\"%s\",\"data\":\"0x%h\",\"check\":\"0x%h\"}",phase_name,data_value,check_value);
  endtask
  task automatic integrity_case;
    int before_b;
    check(PORTS==1 && CHANNELS==1 && RANKS==1,"focused integrity configuration");
    if(INTEGRITY_MODE==1) begin
      before_b=b_count[0];send_aw(0,40,0,64/BYTES);send_w(0,0,64/BYTES);wait_b(0,40,before_b+1);wait_settled();
      inline_snapshot("initial");
      // Faults alter external CA-driven storage, never a DUT ECC result.
      @(negedge clk);memories[0].memory_model.memory_bytes[2]^=8'h80;
      inline_snapshot("single_fault");
      send_ar(0,41,0,64/BYTES);read_result(0,41,0,64/BYTES);wait_settled();
      inline_snapshot("scrubbed");
      repeat(10) @(negedge clk);check(ce_count==1 && ue_count==0,"one correction notification");
      memories[0].memory_model.memory_bytes[3]^=8'hc0;
      inline_snapshot("double_fault");
      send_ar(0,42,0,64/BYTES);read_result(0,42,0,64/BYTES,2);wait_settled();
      before_b=b_count[0];send_aw(0,43,0,64/BYTES);send_w(0,0,64/BYTES,0,0,1,8'h80);wait_b(0,43,before_b+1,2);wait_settled();
      inline_snapshot("rejected_partial");
      repeat(10) @(negedge clk);check(ce_count==1 && ue_count==2,"UE read/partial write notifications");
      before_b=b_count[0];send_aw(0,44,0,64/BYTES);send_w(0,0,64/BYTES,0,0,0,8'hff);wait_b(0,44,before_b+1);wait_settled();
      send_ar(0,45,0,64/BYTES);read_result(0,45,0,64/BYTES);wait_settled();inline_snapshot("rewritten");
      before_b=b_count[0];send_aw(0,46,0,64/BYTES);send_w(0,0,64/BYTES,0,0,1,8'h80);wait_b(0,46,before_b+1);wait_settled();
      send_ar(0,47,0,64/BYTES);read_result(0,47,0,64/BYTES);wait_settled();inline_snapshot("partial_rmw");
      check(!host_fault && stale==0 && !status_value[0].fault,"inline service remains healthy");
      $display("DATAPATH_INLINE_PASS CE=%0d UE=%0d",ce_count,ue_count);
    end else if(INTEGRITY_MODE==2) begin
      // The PHY/device model attests a failed write-link epoch via real MRR43.
      // Raw outgoing parity is checked independently from the bus trace.
      mr43=1;bready[0]=0;before_b=b_count[0];
      send_aw(0,50,0,64/BYTES);send_w(0,0,64/BYTES);
      while(!bvalid[0]) source_negative(0);
      check(b[0].response==2 && memories[0].mr43_count>0,"MR43 failure before B exposure");
      repeat(12) source_negative(0);
      bready[0]=1;wait_b(0,50,before_b+1,2);wait_settled();
      mr43=0;before_b=b_count[0];send_aw(0,51,0,64/BYTES);send_w(0,0,64/BYTES,0,0,0,8'hff);wait_b(0,51,before_b+1);wait_settled();
      send_ar(0,52,0,64/BYTES);read_result(0,52,0,64/BYTES);wait_settled();
      repeat(10) @(negedge clk);check(ue_count==1 && !host_fault && !status_value[0].fault,"link error notification and later clean epoch");
      $display("DATAPATH_WRITE_LINK_PASS UE=%0d MR43=%0d",ue_count,memories[0].mr43_count);
    end else if(INTEGRITY_MODE==4) begin
      int identity_value,expected_ce,expected_ue;
      before_b=b_count[0];send_aw(0,70,0,64/BYTES);send_w(0,0,64/BYTES);wait_b(0,70,before_b+1);wait_settled();
      send_ar(0,71,0,64/BYTES);read_result(0,71,0,64/BYTES);wait_settled();
      identity_value=72;expected_ce=0;expected_ue=0;
      // Every DQ/parity column crosses the real returned-word tracker on each
      // byte slice and on both sides of the BL32 split, including C8.
      for(int group_index=0;group_index<2;group_index++) for(int slice_index=0;slice_index<2;slice_index++) begin
        memories[0].memory_model.read_fault_group=group_index;
        memories[0].memory_model.read_fault_slice=slice_index;
        for(int bit_index=0;bit_index<137;bit_index++) begin
          memories[0].memory_model.read_fault_mask=137'b1<<bit_index;
          send_ar(0,16'(identity_value),0,64/BYTES);read_result(0,16'(identity_value),0,64/BYTES);wait_settled();
          identity_value++;expected_ce++;
        end
        // A DQ/DQ and a DQ/parity double cannot become corrected payload.
        for(int pair_index=0;pair_index<2;pair_index++) begin
          memories[0].memory_model.read_fault_mask=137'b1 | (137'b1<<(pair_index==0 ? 127 : 136));
          send_ar(0,16'(identity_value),0,64/BYTES);read_result(0,16'(identity_value),0,64/BYTES,2);wait_settled();
          identity_value++;expected_ue++;
        end
      end
      memories[0].memory_model.read_fault_mask=0;
      send_ar(0,16'(identity_value),0,64/BYTES);read_result(0,16'(identity_value),0,64/BYTES);wait_settled();
      repeat(20) @(negedge clk);
      check(ce_count==expected_ce && ue_count==expected_ue && !host_fault && !status_value[0].fault,"read-link notifications and continued service");
      $display("DATAPATH_READ_LINK_PASS CE=%0d UE=%0d singles=548 doubles=8 clean=2",ce_count,ue_count);
    end else begin
      // MR27=0x45 admits 64 ACTs per pair; each ordinary REF subtracts 16.
      // Repeat row changes across refresh intervals until residual RAA forces
      // actual RFM. Keep addresses in the programmed one-MiB region.
      for(int n=0;n<256 && memories[0].rfm_count==0;n++) begin
        longint unsigned at;
        at=(64'(n)%64'd32)<<15;before_b=b_count[0];
        send_aw(0,16'(60+n%128),at,64/BYTES);send_w(0,at,64/BYTES);wait_b(0,16'(60+n%128),before_b+1);wait_settled();
      end
      @(negedge clk);enable=0;send_command(CH_CMD_QUIESCE);
      wait(status_value[0].state==1 && status_value[0].drained);
      @(negedge clk);mr4=13;$display("THERMAL_CHANGE ck=%0d",observer.ck);
      send_command(CH_CMD_ENABLE);@(negedge clk);enable=1;
      for(int n=0;n<6000;n++) begin
        @(negedge clk);if(status_value[0].temperature_mr4==13) break;
      end
      check(status_value[0].temperature_mr4==13,"completed hot MR4 before hot traffic");
      wait_settled();
      $display("HOT_WORKLOAD_START ps=%0d",observer.timestamp_ps());
      for(int n=0;n<32;n++) begin
        longint unsigned at;
        // Change rows under active derating and tightened refresh deadlines.
        at=((64'(n)%64'd16)<<15)+64'd512;before_b=b_count[0];
        check(status_value[0].temperature_mr4==13,"hot traffic temperature retained");
        send_aw(0,16'(200+n),at,64/BYTES);send_w(0,at,64/BYTES);
        wait_b(0,16'(200+n),before_b+1);wait_settled();
        send_ar(0,16'(240+n),at,64/BYTES);read_result(0,16'(240+n),at,64/BYTES);wait_settled();
      end
      $display("HOT_WORKLOAD_END ps=%0d",observer.timestamp_ps());
      // Finish at a real simultaneous host/pin/maintenance drain, including
      // any MR4 return independently due after the last host response.
      @(negedge clk);enable=0;send_command(CH_CMD_QUIESCE);
      for(int n=0;n<3000;n++) begin
        @(negedge clk);if(status_value[0].state==1 && status_value[0].drained) break;
      end
      check(status_value[0].state==1 && status_value[0].drained,"hot workload physical tail drain");
      $display("management commands RFM=%0d MR4=%0d REF=%0d ZQ=%0d",memories[0].rfm_count,memories[0].mr4_count,memories[0].refresh_count,memories[0].zq_count);
      check(memories[0].rfm_count>0 && memories[0].mr4_count>0 && memories[0].refresh_count>0 && memories[0].zq_count>0,"physical RFM/MR4/REF/ZQ execution");
      check(!status_value[0].fault && !host_fault,"management remains healthy");
      $display("DATAPATH_MANAGEMENT_PASS RFM=%0d MR4=%0d REF=%0d ZQ=%0d",memories[0].rfm_count,memories[0].mr4_count,memories[0].refresh_count,memories[0].zq_count);
    end
    @(negedge clk);finish_observations=1;repeat(2) @(negedge clk);
    $display("DATAPATH_INTEGRATION_PASS checks=%0d integrity=%0d",checks,INTEGRITY_MODE);$finish;
  endtask
  initial begin
    int before_b;
    if($value$plusargs("fairness_mode=%d",fairness_mode)) begin
      check(fairness_mode inside {0,1,2} && cap_mode==0,"fairness selection");
      if($value$plusargs("fairness_rows=%d",fairness_rows)) check(fairness_rows inside {0,1},"fairness traffic selection");
      if($value$plusargs("fairness_service=%d",fairness_service))
        check(fairness_service inside {1,2,4},"fairness service selection");
      // The reference A32/G64 case remains the single-direction baseline.
      // Mixed row/direction changes have separate measured costs. A1 churn
      // deliberately exercises every class's urgency without forced stalls.
      fairness_age=fairness_rows!=0 ? 1 : 32;
      // Profile4 same-bank BL32 writes need at least 76 CK through write
      // recovery, PRE, ACT and tRCD. Keep the balanced/read G64 baseline;
      // the separately declared write-churn budget must be established by
      // independent replay, including the tail, at every selected divisor.
      fairness_gap=fairness_mode==2 ? 128 : fairness_rows!=0 && fairness_mode==1 ? 96 : 64;
      check($value$plusargs("fairness_observations=%s",fairness_path),"fairness observation path");
      fairness_stream=$fopen(fairness_path,"w");check(fairness_stream!=0,"fairness trace open");
      $fdisplay(fairness_stream,"{\"kind\":\"header\",\"version\":5,\"mode\":%0d,\"divisor\":%0d,\"profile\":%0d,\"rows\":%0d,\"depth\":64,\"age\":%0d,\"tick\":8,\"gap\":%0d}",fairness_mode,fairness_service,PROFILE_NUMBER,fairness_rows,fairness_age,fairness_gap);
    end
    if($value$plusargs("cap_mode=%d",cap_mode)) begin
      check(cap_mode==1 || cap_mode==2,"cap mode");
      if($value$plusargs("cap_granule_bytes=%d",cap_granule_bytes))
        check(cap_granule_bytes==32 || cap_granule_bytes==64,"cap granule size");
      if($value$plusargs("cap_mixed=%d",cap_mixed))
        check(cap_mixed==0 || (cap_mixed==1 && cap_mode==1) || (cap_mixed==2 && RANKS==1),"cap traffic mode");
      if($value$plusargs("zero_cap_direction=%d",zero_cap_direction))
        check(zero_cap_direction inside {0,1} && cap_granule_bytes==64 && cap_mixed==0,"zero cap selection");
      check($value$plusargs("cap_observations=%s",cap_path),"cap observation path");
      cap_stream=$fopen(cap_path,"w");check(cap_stream!=0,"cap trace open");
      if(zero_cap_direction>=0)
        $fdisplay(cap_stream,"{\"kind\":\"header\",\"version\":3,\"mode\":%0d,\"channels\":%0d,\"window\":512,\"zero_direction\":%0d,\"read_cap\":%0d,\"write_cap\":%0d,\"granule_bytes\":64}",
          cap_mode,CHANNELS,zero_cap_direction,zero_cap_direction==0 ? 0 : 64,zero_cap_direction==1 ? 0 : 64);
      else
        $fdisplay(cap_stream,"{\"kind\":\"header\",\"version\":%0d,\"mode\":%0d,\"channels\":%0d,\"window\":512,\"read_cap\":64,\"write_cap\":64,\"granule_bytes\":%0d,\"mixed\":%0d,\"read_weight\":1,\"write_weight\":%0d,\"write_batch_min\":%0d,\"write_batch_max\":%0d}",cap_mixed==2 ? 4 : 2,cap_mode,CHANNELS,cap_granule_bytes,cap_mixed,cap_mixed==2 ? 4 : 1,cap_mixed==2 ? 2 : 0,cap_mixed==2 ? 4 : 0);
    end
    host_cfg='0;cfg='0;mailbox_value='0;management='0;aw='0;ar='0;wd='0;train_token=0;command='0;target_fsp=0;target_frequency=0;phy_refresh_done=0;
    for(int p=0;p<PORTS;p++) begin b_count[p]=0;r_count[p]=0;end
    for(int c=0;c<CHANNELS;c++) zero_admissions[c]=0;
    expected.delete();
    expected_profile(PROFILE_NUMBER);profile.ranks=2'(RANKS);
    for(int c=0;c<CHANNELS;c++) begin
      cfg[c].profile=profile;cfg[c].inline_ecc=INTEGRITY_MODE==1;cfg[c].service_div=3'(SERVICE_DIV);cfg[c].age_tick_div=8;cfg[c].read_urgent_age=32;cfg[c].write_urgent_age=32;
      cfg[c].read_weight=1;cfg[c].write_weight=1;cfg[c].bw_window=3000;cfg[c].config_epoch=1;
      if(fairness_mode>=0) begin
        cfg[c].service_div=3'(fairness_service);
        cfg[c].read_urgent_age=16'(fairness_age);cfg[c].write_urgent_age=16'(fairness_age);
        cfg[c].read_weight=7;cfg[c].write_weight=1;
        cfg[c].read_batch_min=1;cfg[c].write_batch_min=1;cfg[c].write_batch_max=4;
      end
      if(cap_mode!=0) begin
        cfg[c].caps_enable=1;cfg[c].work_conserving=cap_mode==2;
        cfg[c].bw_window=512;cfg[c].read_cap=64;cfg[c].write_cap=64;
        if(zero_cap_direction==0) cfg[c].read_cap=0;
        if(zero_cap_direction==1) cfg[c].write_cap=0;
        if(cap_mixed==2) begin
          cfg[c].read_weight=1;cfg[c].write_weight=4;cfg[c].write_batch_min=2;cfg[c].write_batch_max=4;
        end
      end
    end
    host_cfg.granule64=GRANULE_BYTES==64;host_cfg.inline_ecc=INTEGRITY_MODE==1;host_cfg.config_epoch=1;
    if(cap_mode!=0) host_cfg.granule64=cap_granule_bytes==64;
    if(cap_mode!=0 || fairness_mode>=0) for(int q=0;q<16;q++) host_cfg.qos_map[q]=2'(q%4);
    host_cfg.read_limit='{default:8'(DEPTH)};host_cfg.write_limit='{default:8'(DEPTH)};
    host_cfg.regions[0].enable=1;host_cfg.regions[0].size=RANKS==2 ? 41'd1<<(30+$clog2(CHANNELS)+$clog2(RANKS)) : 41'd1<<20;
    for(int bit_index=0;bit_index<40;bit_index++) host_cfg.regions[0].permutation[bit_index]=6'(bit_index);
    #20;rst_n=1;axi_rst_n='1;memory_reset=0;publish_and_boot();stage=1;
    if(fairness_mode>=0) fairness_case();
    if(zero_cap_direction>=0) zero_cap_case();
    if(cap_mode!=0) cap_case();
    if(INTEGRITY_MODE!=0) integrity_case();
    before_b=b_count[0];send_aw(0,1,4096,128);send_w(0,4096,128);wait_b(0,1,before_b+1);
    send_ar(PORTS-1,2,4096,128);read_result(PORTS-1,2,4096,128);wait_settled();stage=2;
    before_b=b_count[0];send_aw(0,3,8192,64/BYTES);send_w(0,8192,64/BYTES,0,0,1);wait_b(0,3,before_b+1);
    send_ar(PORTS-1,4,8192,64/BYTES);read_result(PORTS-1,4,8192,64/BYTES);wait_settled();stage=3;
    // Quiesce immediately after source acceptance, while payload is still in
    // real CDC. The channel must settle this obligation before closing ingress.
    before_b=b_count[0];send_aw(0,5,12288,64/BYTES);send_w(0,12288,64/BYTES);
    @(negedge clk);enable=0;send_command(CH_CMD_QUIESCE);
    wait_settled();wait_b(0,5,before_b+1);
    for(int c=0;c<CHANNELS;c++) wait(status_value[c].state==1 && status_value[c].drained);
    send_command(CH_CMD_ENABLE);@(negedge clk);enable=1;stage=4;
    send_ar(PORTS-1,6,12288,64/BYTES);read_result(PORTS-1,6,12288,64/BYTES);wait_settled();stage=5;
    check(!host_fault && stale==0,"clean traffic fault/stale completion");
    begin
      longint unsigned total_read,total_write;
      total_read=0;total_write=0;
      for(int c=0;c<CHANNELS;c++) begin
        total_read+=status_value[c].performance.payload_read;total_write+=status_value[c].performance.payload_write;
        check(status_value[c].performance.cycles>0 && status_value[c].performance.commands>0
          && status_value[c].performance.max_latency_ck>0 && status_value[c].performance.max_wait_ck>0,"actual owner performance counters");
      end
      check(total_write==written_bytes && total_read==64'(128*BYTES+128),"completed granule payload accounting");
    end
    if(RANKS==2) begin
      // The identity map puts rank after channel striping. Alternate actual
      // writes and reads across both ranks, retaining bytes on each side.
      for(int n=0;n<4;n++) begin
        longint unsigned at;
        at=64'd16384+(n%2==0 ? 64'd0 : 64'd1<<(30+$clog2(CHANNELS)));
        before_b=b_count[0];send_aw(0,16'(20+n),at,64/BYTES);send_w(0,at,64/BYTES);wait_b(0,16'(20+n),before_b+1);
        send_ar(PORTS-1,16'(24+n),at,64/BYTES);read_result(PORTS-1,16'(24+n),at,64/BYTES);wait_settled();
      end
      $display("DATAPATH_RANK_SWITCH_PASS ranks=2 transfers=8");
    end
    parameter_boundaries();
    @(negedge clk);finish_observations=1;repeat(2) @(negedge clk);
    $display("DATAPATH_INTEGRATION_PASS checks=%0d ports=%0d channels=%0d ranks=%0d width=%0d depth=%0d profile=%0d div=%0d",checks,PORTS,CHANNELS,RANKS,DATA_BITS,DEPTH,PROFILE_NUMBER,SERVICE_DIV);$finish;
  end
  initial begin #(INTEGRITY_MODE==4 ? 1000000 : INTEGRITY_MODE==0 ? 30000 : 100000);$fatal(1,"datapath deadline stage%0d settled=%0d fault=%0d",stage,settled,host_fault);end
endmodule
