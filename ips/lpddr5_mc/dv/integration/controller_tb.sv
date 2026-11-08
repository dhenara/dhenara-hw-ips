module controller_tb #(
  parameter int PORTS=2,CHANNELS=2,RANKS=1,DATA_BITS=128,DEPTH=16,
  parameter int PROFILE_NUMBER=0,SERVICE_DIV=2,
  parameter bit BENCHMARK=0,RUNTIME_RESET=0,RESET_MATRIX=0
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_host_pkg::*;
  import lpddr5_mc_apb_metadata_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  localparam int BYTES=DATA_BITS/8;
  logic clk=0,rst_n=0,enable,run_ck=1,run_pclk=1;
  logic [PORTS-1:0] run_axi='1;
  always #0.625 if(run_ck) clk<=~clk;
  logic [PORTS-1:0] axi_clk=0,axi_rst_n=0,monitor_rst_n;
  for(genvar p=0;p<PORTS;p++) begin : clocks
    always #(BENCHMARK ? 1.25 : 1.25+0.5*p) if(run_axi[p]) axi_clk[p]<=~axi_clk[p];
    lpddr5_mc_reset_sync monitor_reset(.clk_i(axi_clk[p]),.arst_ni(axi_rst_n[p]),.rst_no(monitor_rst_n[p]));
  end
  logic settled,host_fault,irq,reset_request,external_fault=0;
  logic [CHANNELS-1:0] reset_confirmed='1;
  logic [PORTS-1:0] awvalid=0,arvalid=0,wvalid=0,awready,arready,wready,bvalid,rvalid,bready='1,rready=0;
  lpddr5_mc_axi_address_t [PORTS-1:0] aw,ar;
  lpddr5_mc_axi_write_t [PORTS-1:0] wd;
  lpddr5_mc_axi_b_t [PORTS-1:0] b;
  lpddr5_mc_axi_read_t [PORTS-1:0] r;
  lpddr5_mc_profile_t [2:0] qualified;
  lpddr5_mc_register_image_t staging_image;
  logic pclk=0,psel=0,penable=0,pready;
  always #3.5 if(run_pclk) pclk<=~pclk;
  lpddr5_mc_apb_request_t apb_request='0;
  lpddr5_mc_apb_response_t apb_response;
  logic [31:0] read_data;
  logic observe_traffic=0,observe_recovery=0,finish_recovery=0;
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
  logic [CHANNELS-1:0][4:0] mr4_value;
  byte unsigned expected[65536];
  int b_count[PORTS],r_count[PORTS],checks=0,stage=0;
  int power_policy=0;
  logic [CHANNELS-1:0] power_entered=0,power_exited=0,update_seen=0;
  logic [PORTS-1:0][15:0] last_bid;
  logic [PORTS-1:0][1:0] last_bresponse;
  logic unused_debug;
  assign unused_debug=^{irq,management_out,wck};
  initial forever begin
    @(negedge clk);
    if(rst_n && power_policy!=0) for(int c=0;c<CHANNELS;c++) begin
      management[c].lp_ctrl_ack=management_out[c].lp_ctrl_req;
      management[c].lp_data_ack=management_out[c].lp_data_req;
      if(management_out[c].ctrlupd_req) update_seen[c]=1;
      if(ca[c].cs!=0 && ca[c].address[6:0]==(power_policy==1 ? 7'd64 : 7'd104)) power_entered[c]=1;
      if(power_policy==2 && ca[c].cs!=0 && ca[c].address[6:0]==40) begin
        check(update_seen[c],"actual update request must precede SRX");power_exited[c]=1;
      end
      if(power_policy==1 && power_entered[c] && ca[c].cs!=0 && ca[c].address==0) power_exited[c]=1;
    end
  end
  lpddr5_mc #(.PORTS(PORTS),.CHANNELS(CHANNELS),.RANKS(RANKS),.DATA_BITS(DATA_BITS),.DEPTH(DEPTH),.ID_BITS(BENCHMARK ? 16 : 8)) dut (
    .clk_ck_i(clk),.pclk_i(pclk),.rst_ni(rst_n),.psel_i(psel),.penable_i(penable),.apb_i(apb_request),.pready_o(pready),.apb_o(apb_response),.irq_o(irq),
    .qualified_profiles_i(qualified),.external_fault_i(external_fault),.destructive_reset_request_o(reset_request),.destructive_reset_confirmed_i(reset_confirmed),
    .axi_clk_i(axi_clk),.axi_rst_ni(axi_rst_n),.awvalid_i(awvalid),.arvalid_i(arvalid),.wvalid_i(wvalid),.awready_o(awready),.arready_o(arready),.wready_o(wready),
    .aw_i(aw),.ar_i(ar),.w_i(wd),.bvalid_o(bvalid),.rvalid_o(rvalid),.bready_i(bready),.rready_i(rready),.b_o(b),.r_o(r),
    .management_i(management),.rank_report_valid_i(report_valid),.rank_self_refresh_i(report_sr),.phy_refresh_done_i(phy_refresh_done),.management_o(management_out),
    .ca_o(ca),.wck_o(wck),.write_o(writes),.read_request_o(reads),.return_i(returns),.status_o(status_value),
    .enabled_o(enable),.fault_o(host_fault),.host_settled_o(settled));
  lpddr5_mc_observer #(.PORTS(PORTS),.CHANNELS(CHANNELS),.DATA_BITS(DATA_BITS),.OBSERVE_CYCLES(BENCHMARK)) observer (
    .clk_ck_i(clk),.rst_ni(observe_traffic),.axi_clk_i(axi_clk),.axi_rst_ni(monitor_rst_n),
    .awvalid_i(awvalid),.arvalid_i(arvalid),.wvalid_i(wvalid),.awready_i(awready),.arready_i(arready),.wready_i(wready),
    .aw_i(aw),.ar_i(ar),.w_i(wd),.bvalid_i(bvalid),.rvalid_i(rvalid),.bready_i(bready),.rready_i(rready),.b_i(b),.r_i(r),
    .ca_i(ca),.wck_i(wck),.write_i(writes),.read_i(reads),.return_i(returns),.finish_i(finish_observations));
  lpddr5_mc_observer #(.PORTS(PORTS),.CHANNELS(CHANNELS),.DATA_BITS(DATA_BITS),.FILE_ARGUMENT("recovery_observations=%s")) recovery_observer (
    .clk_ck_i(clk),.rst_ni(observe_recovery),.axi_clk_i(axi_clk),.axi_rst_ni(monitor_rst_n),
    .awvalid_i(awvalid),.arvalid_i(arvalid),.wvalid_i(wvalid),.awready_i(awready),.arready_i(arready),.wready_i(wready),
    .aw_i(aw),.ar_i(ar),.w_i(wd),.bvalid_i(bvalid),.rvalid_i(rvalid),.bready_i(bready),.rready_i(rready),.b_i(b),.r_i(r),
    .ca_i(ca),.wck_i(wck),.write_i(writes),.read_i(reads),.return_i(returns),.finish_i(finish_recovery));
  for(genvar c=0;c<CHANNELS;c++) begin : memories
    int mr43_count,mr4_count,zq_count,refresh_count,rfm_count;
    logic unused_counts;
    assign unused_counts=^{mr43_count,mr4_count,zq_count,refresh_count,rfm_count};
    lpddr5_mc_channel_memory #(.INITIAL_PATTERN(BENCHMARK),.CHANNELS(CHANNELS),.CHANNEL_INDEX(c)) memory_model (
      .profile_i(qualified[0]),.ca_i(ca[c]),.write_i(writes[c]),.read_i(reads[c]),.return_o(returns[c]),
      .mr43_i(8'd0),.mr4_i(mr4_value[c]),.mr43_count_o(mr43_count),.mr4_count_o(mr4_count),.zq_count_o(zq_count),.refresh_count_o(refresh_count),.rfm_count_o(rfm_count));
    initial forever begin
      @(negedge clk);
      if(memory_reset) begin
        memories[c].memory_model.reset();report_valid[c]=2'b11>>(2-RANKS);report_sr[c]=0;
      end else begin memories[c].memory_model.prepare();memories[c].memory_model.sample();end
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
  function automatic lpddr5_mc_axi_address_t address(input logic [15:0] id,input int at,beats);
    lpddr5_mc_axi_address_t value;
    value='0;value.id=16'(id);value.address=40'(at);value.len=8'(beats-1);value.size=3'($clog2(BYTES));value.burst=1;return value;
  endfunction
  task automatic send_aw(input int p,input logic [15:0] id,input int at,beats);
    source_negative(p);aw[p]=address(id,at,beats);awvalid[p]=1;
    do source_positive(p);while(!awready[p]);source_negative(p);awvalid[p]=0;
  endtask
  task automatic send_ar(input int p,input logic [15:0] id,input int at,beats);
    source_negative(p);ar[p]=address(id,at,beats);arvalid[p]=1;
    do source_positive(p);while(!arready[p]);source_negative(p);arvalid[p]=0;
  endtask
  task automatic send_w(input int p,at,beats,start=0,limit=0,input bit sparse=0);
    int finish_at;
    lpddr5_mc_axi_write_t value;
    finish_at=limit==0 ? beats : limit;
    source_negative(p);wvalid[p]=1;
    for(int n=start;n<finish_at;n++) begin
      value='0;value.last=n==beats-1;
      for(int j=0;j<BYTES;j++) begin
        value.strobe[j]=!sparse || (n+j)%3!=0;value.data[j*8+:8]=8'((at+n*BYTES+j)^((at+n*BYTES+j)>>8)^90);
        if(value.strobe[j]) expected[at+n*BYTES+j]=value.data[j*8+:8];
      end
      wd[p]=value;do source_positive(p);while(!wready[p]);source_negative(p);
    end
    wvalid[p]=0;
  endtask
  task automatic wait_b(input int p,input logic [15:0] id,input int count,input logic [1:0] response=0);
    while(b_count[p]<count) source_negative(p);
    check(last_bid[p]==16'(id) && last_bresponse[p]==2'(response),"B identity/status");
  endtask
  task automatic read_result(input int p,input logic [15:0] id,input int at,beats,input logic [1:0] response=0);
    source_negative(p);rready[p]=1;
    for(int n=0;n<beats;n++) begin
      do source_positive(p);while(!rvalid[p]);
      check(r[p].id==16'(id) && r[p].last==(n==beats-1) && r[p].response==2'(response),"R identity/count/status");
      for(int j=0;j<BYTES;j++) check(r[p].data[j*8+:8]==(response==0 ? expected[at+n*BYTES+j] : 8'd0),"independent byte scoreboard");
      source_negative(p);
    end
    rready[p]=0;
  endtask
  task automatic access(input bit write_value,input logic [17:0] address_value,input logic [31:0] value,input bit expected_error=0);
    @(negedge pclk);psel=1;penable=0;
    apb_request='{address:address_value,write:write_value,data:value,strobe:write_value ? 4'hf : 4'd0,protection:3'd0};
    @(negedge pclk);penable=1;#0.1;
    check(pready && apb_response.error==expected_error,$sformatf("APB address=%h write=%b value=%h ready=%b error=%b expected=%b boot=%0d apb_fault=%b global=%h",
      address_value,write_value,value,pready,apb_response.error,expected_error,status_value[0].boot_epoch,dut.apb_fault,dut.global_pq));read_data=apb_response.data;
    @(negedge pclk);psel=0;penable=0;
  endtask
  task automatic write_csr(input int index_value,input logic [31:0] value);
    check(index_value>=0 && index_value<REG_COUNT,"CSR index");
    access(1,18'(REG_OFFSET[index_value]),value);
  endtask
  task automatic wait_status(input int index_value,input logic [31:0] mask_value,expected_value);
    for(int n=0;n<500;n++) begin
      access(0,18'(REG_OFFSET[index_value]),0);
      if((read_data & mask_value)==expected_value) return;
    end
    $fatal(1,"stage%0d APB status deadline index%0d value%h",stage,index_value,read_data);
  endtask
  task automatic publish_and_boot(input int boot_value);
    write_csr(INDEX_CONFIG_COMMIT,1);
    for(int n=0;n<10000;n++) begin
      @(negedge clk);if(dut.host_config_active && dut.host_initialized && dut.config_epoch!=0) break;
      check(!host_fault,"publication/host installation fault");
    end
    check(dut.host_config_active && dut.host_initialized,"actual host image and transport installation");
    // Old training epochs and a stale high completion cannot authorize boot.
    if(boot_value!=0) begin
      for(int c=0;c<CHANNELS;c++) management[c].init_complete=1;
      repeat(20) @(negedge clk);
      check(!status_value[0].train_request && !status_value[0].memory_trusted,"stale PHY epoch authorized boot");
    end
    for(int c=0;c<CHANNELS;c++) begin management[c].init_complete=0;management[c].training_epoch=32'(boot_value);end
    repeat(4) @(negedge clk);
    for(int c=0;c<CHANNELS;c++) management[c].init_complete=1;
    wait_status(INDEX_TRAIN_REQUEST,1,1);
    access(0,18'(REG_OFFSET[INDEX_TRAIN_DONE]),0);
    check(read_data==0 && !enable,"training must remain incomplete before matching ACK");
    if(boot_value!=0) access(1,18'(REG_OFFSET[INDEX_TRAIN_ACK]),1,1);
    write_csr(INDEX_TRAIN_TOKEN,32'(boot_value));write_csr(INDEX_TRAIN_ACK,1);
    wait_status(INDEX_TRAIN_DONE,1,1);
    wait_status(INDEX_STATUS,32'hb,32'hb);
    check(!enable,"TRAIN_DONE must be observable before ENABLE");
    for(int c=0;c<CHANNELS;c++) begin
      expect_channel(c,INDEX_DEVICE_MR8,32'(qualified[0].mr8));
      expect_channel(c,INDEX_DEVICE_MR27,32'(qualified[0].mr27));
      expect_channel(c,INDEX_DEVICE_MR57,32'(qualified[0].mr57));
    end
    write_csr(INDEX_CONTROL,1);
    for(int n=0;n<300;n++) begin @(negedge clk);if(enable) break;end
    check(enable && !host_fault,"APB ENABLE to actual source admission");
  endtask
  task automatic expect_channel(input int channel,index_value,input logic [31:0] expected_value);
    for(int n=0;n<500;n++) begin
      access(0,18'h10000+18'(channel)*18'h10000+18'(REG_OFFSET[index_value]),0);
      if(read_data==expected_value) return;
    end
    $fatal(1,"stage%0d channel%0d CSR%0d actual%h expected%h",stage,channel,index_value,read_data,expected_value);
  endtask
  task automatic wait_settled;
    for(int n=0;n<3000;n++) begin @(negedge clk);if(settled) return;end
    $fatal(1,"stage%0d accepted work failed to drain",stage);
  endtask
  `include "benchmark.svh"
  `include "runtime_reset.svh"
  initial begin
    int before_b;
    string path;
    int stream,count,number,ranks,target;
    logic [31:0] word_value;
    management='0;aw='0;ar='0;wd='0;phy_refresh_done=0;
    for(int c=0;c<CHANNELS;c++) mr4_value[c]=9;
    for(int p=0;p<PORTS;p++) begin b_count[p]=0;r_count[p]=0;end
    for(int i=0;i<65536;i++) expected[i]=0;
    expected_profile(PROFILE_NUMBER);profile.ranks=2'(RANKS);profile.mr57=3;
    check(profile_valid(profile),"nonzero paired-bank MR57 descriptor must be valid");
    for(int f=0;f<3;f++) qualified[f]=profile;
    check($value$plusargs("images=%s",path),"missing image vectors");stream=$fopen(path,"r");
    check(stream!=0 && $fscanf(stream,"%d",count)==1 && count==192,"image vector header");
    // Select all three FSP banks for the exact requested profile/geometry.
    for(int image_index=0;image_index<count;image_index++) begin
      check($fscanf(stream,"%d %d %d",number,ranks,target)==3,"image identity");
      for(int regindex=0;regindex<REG_COUNT;regindex++) begin
        check($fscanf(stream,"%h",word_value)==1,"image word");
        if(number==PROFILE_NUMBER && ranks==RANKS && (target==0 || REG_FSP[regindex]==target)) staging_image[regindex]=word_value;
      end
    end
    $fclose(stream);staging_image[INDEX_FSP_VALID]=7;
    staging_image[INDEX_MODE][10:8]=3'(SERVICE_DIV);
    staging_image[INDEX_PORT_LIMIT_0]=32'(DEPTH) | (32'(DEPTH)<<8);
    staging_image[INDEX_PORT_LIMIT_1]=staging_image[INDEX_PORT_LIMIT_0];
    staging_image[INDEX_WR_HIGH]=32'(DEPTH);staging_image[INDEX_WR_LOW]=32'(DEPTH/2);
    if(BENCHMARK) begin
      check($value$plusargs("benchmark_direction=%d",benchmark_direction),"missing benchmark direction");
      check($value$plusargs("benchmark_service=%d",benchmark_service),"missing benchmark service");
      staging_image[INDEX_MODE][10:8]=3'(benchmark_service);
    end
    if($value$plusargs("power_policy=%d",power_policy)) begin
      check(power_policy inside {1,2},"power policy scenario");
      staging_image[INDEX_PM_REQUEST]=32'(power_policy);staging_image[INDEX_PD_IDLE]=256;staging_image[INDEX_SR_IDLE]=256;
    end
    #20;rst_n=1;axi_rst_n='1;memory_reset=0;
    wait(dut.p_started_q && dut.c_started_q && settled);
    repeat(20) @(negedge pclk);
    access(0,18'(REG_OFFSET[INDEX_STATUS]),0);check(read_data==0,"actual cold disabled STATUS");
    access(0,18'(REG_OFFSET[INDEX_TRAIN_DONE]),0);check(read_data==0,"cold training readback");
    for(int c=0;c<CHANNELS;c++) begin
      expect_channel(c,INDEX_DEVICE_MR8,0);expect_channel(c,INDEX_DEVICE_MR27,0);
      expect_channel(c,INDEX_DEVICE_MR57,0);expect_channel(c,INDEX_TEMP_MR4,0);
    end
    for(int regindex=0;regindex<REG_COUNT;regindex++) if(REG_RW[regindex]!=0) begin
      if(REG_SHARED[regindex]) write_csr(regindex,staging_image[regindex]&REG_RW[regindex]);
      else for(int c=0;c<CHANNELS;c++) access(1,18'h10000+18'(c)*18'h10000+18'(REG_OFFSET[regindex]),staging_image[regindex]&REG_RW[regindex]);
    end
    // The independent pin/byte checker starts at completed PHY/software
    // handover. PHY-owned initialization and fresh-epoch admission above are
    // checked by this fixture; no controller refresh clock runs before it.
    publish_and_boot(0);stage=1;observe_traffic=1;
    if(BENCHMARK) benchmark_run();
    if(RUNTIME_RESET) runtime_reset_run();
    before_b=b_count[0];send_aw(0,1,4096,128);send_w(0,4096,128);wait_b(0,1,before_b+1);
    send_ar(PORTS-1,2,4096,128);read_result(PORTS-1,2,4096,128);wait_settled();stage=2;
    if(power_policy!=0) begin
      for(int n=0;n<2000;n++) begin
        @(negedge clk);if((&power_entered) && (&dut.command_ready) && !enable && settled) break;
        check(!host_fault,"automatic low-power entry fault");
      end
      check((&power_entered) && !enable && settled,"actual power policy entry and host drain");
      repeat(40) @(negedge clk);
      // A held, unaccepted AR crosses only as a one-bit wake indication.
      // Its actual source handshake and returned bytes are checked below.
      send_ar(PORTS-1,3,4096,64/BYTES);read_result(PORTS-1,3,4096,64/BYTES);wait_settled();
      check((&power_exited) && !host_fault && enable,"power exit and fresh RUN service");
      @(negedge clk);finish_observations=1;repeat(2) @(negedge clk);
      $display("CONTROLLER_INTEGRATION_PASS checks=%0d power_policy=%0d update=%b",checks,power_policy,update_seen);$finish;
    end
    before_b=b_count[0];send_aw(0,3,8192,64/BYTES);send_w(0,8192,64/BYTES,0,0,1);wait_b(0,3,before_b+1);
    send_ar(PORTS-1,4,8192,64/BYTES);read_result(PORTS-1,4,8192,64/BYTES);wait_settled();stage=3;
    // Quiesce immediately after source acceptance, while payload is still in
    // real CDC. The channel must settle this obligation before closing ingress.
    before_b=b_count[0];send_aw(0,5,12288,64/BYTES);send_w(0,12288,64/BYTES);
    write_csr(INDEX_CONTROL,2);
    wait_settled();wait_b(0,5,before_b+1);
    for(int c=0;c<CHANNELS;c++) wait(status_value[c].state==1 && status_value[c].drained);
    write_csr(INDEX_CONTROL,1);wait(enable);stage=4;
    send_ar(PORTS-1,6,12288,64/BYTES);read_result(PORTS-1,6,12288,64/BYTES);wait_settled();stage=5;
    check(!host_fault && dut.stale_completions==0,"clean traffic fault/stale completion");
    @(negedge clk);finish_observations=1;repeat(2) @(negedge clk);
    // Observe actual returned MR4 data after the traffic trace is closed.
    // Distinct channels and two changing values catch constant/substituted
    // snapshots. The normal traffic oracle keeps its original thermal basis.
    for(int c=0;c<CHANNELS;c++) mr4_value[c]=5'(9+c);
    for(int c=0;c<CHANNELS;c++) expect_channel(c,INDEX_TEMP_MR4,32'(9+c));
    for(int c=0;c<CHANNELS;c++) mr4_value[c]=5'(11+c);
    for(int c=0;c<CHANNELS;c++) expect_channel(c,INDEX_TEMP_MR4,32'(11+c));
    $display("ACTUAL_APB_STATUS_PASS boot=0 mr57=3 changing_mr4_channels=%0d",CHANNELS);
    stage=6;
    // Both source obligations are accepted before ownership is lost. AW has
    // no W yet; AR is still crossing CDC. Neither may disappear during reset.
    // Different low-12-bit granules avoid the intentional 4-KiB lease hash alias.
    bready=0;before_b=b_count[0];send_aw(0,10,16384,64/BYTES);send_ar(PORTS-1,11,20544,64/BYTES);
    @(negedge clk);external_fault=1;repeat(8) @(negedge clk);
    check(host_fault && !enable && !settled,"ownership loss must fence and retain accepted work");
    write_csr(INDEX_CONTROL,8);
    repeat(20) begin @(negedge clk);check(!reset_request,"destructive reset before complete host settlement");end
    send_w(0,16384,64/BYTES);
    read_result(PORTS-1,11,20544,64/BYTES,2);
    bready='1;wait_b(0,10,before_b+1,2);wait_settled();
    for(int n=0;n<100;n++) begin @(negedge clk);if(reset_request) break;end
    check(reset_request,"settled ownership loss must request destructive reset");
    repeat(8) begin @(negedge clk);check(reset_request && status_value[0].boot_epoch==0,"stale high reset confirmation accepted");end
    external_fault=0;reset_confirmed=0;memory_reset=1;
    repeat(4) @(negedge clk);reset_confirmed='1;
    for(int n=0;n<300;n++) begin @(negedge clk);if(!host_fault && status_value[0].boot_epoch==1) break;end
    check(!host_fault && status_value[0].boot_epoch==1 && status_value[1].boot_epoch==1
      && !enable && !dut.host_config_active && !status_value[0].config_valid,"destructive recovery epoch/profile invalidation");
    memory_reset=0;for(int i=0;i<65536;i++) expected[i]=0;
    wait_status(INDEX_BOOT_EPOCH,32'hffffffff,1);
    repeat(30) @(negedge pclk);
    access(0,18'(REG_OFFSET[INDEX_TRAIN_DONE]),0);check(read_data==0,"old training survived destructive recovery");
    for(int c=0;c<CHANNELS;c++) begin
      expect_channel(c,INDEX_DEVICE_MR8,0);expect_channel(c,INDEX_DEVICE_MR27,0);
      expect_channel(c,INDEX_DEVICE_MR57,0);expect_channel(c,INDEX_TEMP_MR4,0);
      mr4_value[c]=9;
    end
    // Fresh rediscovery may change capability facts only after invalidation.
    for(int f=0;f<3;f++) qualified[f].mr57=2;
    publish_and_boot(1);stage=7;observe_recovery=1;
    before_b=b_count[0];send_aw(0,12,24576,64/BYTES);send_w(0,24576,64/BYTES);wait_b(0,12,before_b+1);
    send_ar(PORTS-1,13,24576,64/BYTES);read_result(PORTS-1,13,24576,64/BYTES);wait_settled();
    @(negedge clk);finish_recovery=1;repeat(2) @(negedge clk);
    for(int c=0;c<CHANNELS;c++) expect_channel(c,INDEX_TEMP_MR4,9);
    check(!host_fault,"fresh MR4 polling after recovery");
    $display("ACTUAL_APB_STATUS_PASS boot=1 mr57=2 fresh_mr4=9");
    $display("CONTROLLER_INTEGRATION_PASS checks=%0d boot=%0d",checks,status_value[0].boot_epoch);$finish;
  end
  // Retain useful owner state even if a process deadline interrupts simulation.
  initial forever begin
    #5000;
    $display("CONTROLLER_PROGRESS stage=%0d settled=%0d fault=%0d coordinator=%0d channels=%0d/%0d reset=%0d boot=%0d",
      stage,settled,host_fault,dut.coordinator.state_q,status_value[0].state,status_value[CHANNELS-1].state,reset_request,status_value[0].boot_epoch);
    $fflush();
  end
  initial begin #(BENCHMARK ? 650000 : RESET_MATRIX ? 600000 : RUNTIME_RESET ? 200000 : 65000);$fatal(1,"controller deadline stage%0d settled=%0d fault=%0d coordinator=%0d",stage,settled,host_fault,dut.coordinator.state_q);end
endmodule
