module datapath_tb #(
  parameter int PORTS=2,CHANNELS=2,RANKS=1,DATA_BITS=128,DEPTH=16,
  parameter int PROFILE_NUMBER=0,SERVICE_DIV=2,INTEGRITY_MODE=0
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
  longint unsigned written_bytes=0;
  logic [PORTS-1:0][15:0] last_bid;
  logic [PORTS-1:0][1:0] last_bresponse;
  logic unused_debug;
  assign unused_debug=^{axi_clk,command_rejected,prepare_error,fsp_ready,fsp_declined,prepared_epoch,committed_epoch,notification_valid,
    notifications,management_out,wck,late_error,stale,accepted,grants};
  lpddr5_mc_datapath #(.PORTS(PORTS),.CHANNELS(CHANNELS),.RANKS(RANKS),.DATA_BITS(DATA_BITS),.DEPTH(DEPTH)) dut (
    .clk_ck_i(clk),.rst_ni(rst_n),.enable_i(enable),.fatal_i(fatal),.recover_i(recover),.rearm_i(rearm),
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
  function automatic lpddr5_mc_axi_address_t address(input logic [15:0] id,input longint unsigned at,input int beats);
    lpddr5_mc_axi_address_t value;
    assert(at<64'd1<<40 && beats>=1 && beats<=256) else $fatal(1,"source address/length bounds");
    value='0;value.id=16'(id);value.address=40'(at);value.len=8'(beats-1);value.size=3'($clog2(BYTES));value.burst=1;return value;
  endfunction
  task automatic send_aw(input int p,input logic [15:0] id,input longint unsigned at,input int beats);
    source_negative(p);aw[p]=address(id,at,beats);awvalid[p]=1;
    do source_positive(p);while(!awready[p]);source_negative(p);awvalid[p]=0;
  endtask
  task automatic send_ar(input int p,input logic [15:0] id,input longint unsigned at,input int beats);
    source_negative(p);ar[p]=address(id,at,beats);arvalid[p]=1;
    do source_positive(p);while(!arready[p]);source_negative(p);arvalid[p]=0;
  endtask
  task automatic send_w(input int p,input longint unsigned at,input int beats,start=0,limit=0,input bit sparse=0,input byte unsigned salt=0);
    int finish_at;
    lpddr5_mc_axi_write_t value;
    finish_at=limit==0 ? beats : limit;
    source_negative(p);wvalid[p]=1;
    for(int n=start;n<finish_at;n++) begin
      value='0;value.last=n==beats-1;
      for(int j=0;j<BYTES;j++) begin
        value.strobe[j]=!sparse || (n+j)%3!=0;value.data[j*8+:8]=8'((at+64'(n)*64'(BYTES)+64'(j))^((at+64'(n)*64'(BYTES)+64'(j))>>8)^90)^salt;
        if(value.strobe[j]) expected[at+64'(n)*64'(BYTES)+64'(j)]=value.data[j*8+:8];
      end
      wd[p]=value;do source_positive(p);while(!wready[p]);written_bytes+=64'($countones(value.strobe));source_negative(p);
    end
    wvalid[p]=0;
  endtask
  task automatic wait_b(input int p,input logic [15:0] id,input int count,input logic [1:0] response=0);
    while(b_count[p]<count) source_negative(p);
    check(last_bid[p]==16'(id) && last_bresponse[p]==2'(response),"B identity/status");
  endtask
  task automatic read_result(input int p,input logic [15:0] id,input longint unsigned at,input int beats,input logic [1:0] response=0);
    source_negative(p);rready[p]=1;
    for(int n=0;n<beats;n++) begin
      do source_positive(p);while(!rvalid[p]);
      check(r[p].id==16'(id) && r[p].last==(n==beats-1) && r[p].response==2'(response),"R identity/count/status");
      for(int j=0;j<BYTES;j++) check(r[p].data[j*8+:8]==(response==0 ? (expected.exists(at+64'(n)*64'(BYTES)+64'(j))!=0 ? expected[at+64'(n)*64'(BYTES)+64'(j)] : 8'd0) : 8'd0),"independent byte scoreboard");
      source_negative(p);
    end
    rready[p]=0;
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
      repeat(3500) @(negedge clk);
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
    host_cfg='0;cfg='0;mailbox_value='0;management='0;aw='0;ar='0;wd='0;train_token=0;command='0;target_fsp=0;target_frequency=0;phy_refresh_done=0;
    for(int p=0;p<PORTS;p++) begin b_count[p]=0;r_count[p]=0;end
    expected.delete();
    expected_profile(PROFILE_NUMBER);profile.ranks=2'(RANKS);
    for(int c=0;c<CHANNELS;c++) begin
      cfg[c].profile=profile;cfg[c].inline_ecc=INTEGRITY_MODE==1;cfg[c].service_div=3'(SERVICE_DIV);cfg[c].age_tick_div=8;cfg[c].read_urgent_age=32;cfg[c].write_urgent_age=32;
      cfg[c].read_weight=1;cfg[c].write_weight=1;cfg[c].bw_window=3000;cfg[c].config_epoch=1;
    end
    host_cfg.granule64=1;host_cfg.inline_ecc=INTEGRITY_MODE==1;host_cfg.config_epoch=1;
    host_cfg.read_limit='{default:8'(DEPTH)};host_cfg.write_limit='{default:8'(DEPTH)};
    host_cfg.regions[0].enable=1;host_cfg.regions[0].size=RANKS==2 ? 41'd1<<(30+$clog2(CHANNELS)+$clog2(RANKS)) : 41'd1<<20;
    for(int bit_index=0;bit_index<40;bit_index++) host_cfg.regions[0].permutation[bit_index]=6'(bit_index);
    #20;rst_n=1;axi_rst_n='1;memory_reset=0;publish_and_boot();stage=1;
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
    @(negedge clk);finish_observations=1;repeat(2) @(negedge clk);
    $display("DATAPATH_INTEGRATION_PASS checks=%0d ports=%0d channels=%0d ranks=%0d width=%0d depth=%0d profile=%0d div=%0d",checks,PORTS,CHANNELS,RANKS,DATA_BITS,DEPTH,PROFILE_NUMBER,SERVICE_DIV);$finish;
  end
  initial begin #(INTEGRITY_MODE==0 ? 30000 : 100000);$fatal(1,"datapath deadline stage%0d settled=%0d fault=%0d",stage,settled,host_fault);end
endmodule
