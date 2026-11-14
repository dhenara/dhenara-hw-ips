// The wrapper, APB publication, real host CDC and channel remain connected.
// Hierarchical state selects injection instants only. Source responses and
// external CA/data/check images are retained for independent replay.
task automatic inline_payload(input int p,at,input byte salt,input bit sparse=0);
  lpddr5_mc_axi_write_t value;
  source_negative(p);wvalid[p]=1;
  for(int n=0;n<64/BYTES;n++) begin
    value='0;value.last=n==64/BYTES-1;
    for(int j=0;j<BYTES;j++) begin
      value.strobe[j]=!sparse || (n*BYTES+j)%3!=0;
      value.data[j*8+:8]=8'(at+n*BYTES+j)^salt;
      if(value.strobe[j]) expected[at+n*BYTES+j]=value.data[j*8+:8];
    end
    wd[p]=value;do source_positive(p);while(!wready[p]);source_negative(p);
  end
  wvalid[p]=0;
endtask
task automatic inline_image(input string phase_name,input int channel,at);
  logic [511:0] data_value,check_value;
  longint unsigned local_at;
  local_at=(64'(at)&64'd63)|((64'(at)>>7)<<6);data_value=0;check_value=0;
  // Constant generate indices are required by the qualified simulator.
  for(int j=0;j<64;j++) begin
    if(channel==0) begin
      data_value[j*8+:8]=memories[0].memory_model.memory_bytes[local_at+64'(j)];
      check_value[j*8+:8]=memories[0].memory_model.memory_bytes[64'((1073741824/576)*512)+(local_at/512)*64+64'(j)];
    end else begin
      data_value[j*8+:8]=memories[1].memory_model.memory_bytes[local_at+64'(j)];
      check_value[j*8+:8]=memories[1].memory_model.memory_bytes[64'((1073741824/576)*512)+(local_at/512)*64+64'(j)];
    end
  end
  $display("INLINE_IMAGE {\"phase\":\"%s\",\"ps\":%0d,\"channel\":%0d,\"address\":%0d,\"data\":\"0x%h\",\"check\":\"0x%h\"}",
    phase_name,observer.timestamp_ps(),channel,at,data_value,check_value);
endtask
task automatic inline_lifecycle_run;
  int before_b,before_failed_b;
  byte saved[64];
  check(PORTS==2 && CHANNELS==2 && RANKS==1 && DATA_BITS==128,"inline lifecycle shape");
  // Four distinct data granules share one physical 64-byte check line.
  for(int n=0;n<4;n++) begin
    before_b=b_count[n%2];send_aw(n%2,16'(20+n),128*n,64/BYTES);
    inline_payload(n%2,128*n,8'(32+n));wait_b(n%2,16'(20+n),before_b+1);wait_settled();
  end
  for(int n=0;n<4;n++) begin
    before_b=b_count[n%2];send_aw(n%2,16'(30+n),128*n,64/BYTES);
    inline_payload(n%2,128*n,8'(80+n),1);wait_b(n%2,16'(30+n),before_b+1);wait_settled();
    send_ar(1-n%2,16'(40+n),128*n,64/BYTES);read_result(1-n%2,16'(40+n),128*n,64/BYTES);wait_settled();
  end
  for(int j=0;j<64;j++) saved[j]=expected[j];
  @(negedge clk);memories[0].memory_model.memory_bytes[2]^=8'h80;
  continuous_memory_flip(0,2,8'h80);
  $display("INLINE_FLIP ps=%0d byte=2 mask=128",observer.timestamp_ps());
  // Same-port younger data is buffered while the older correction scrubs.
  drive_bready(0);before_b=b_count[0];send_ar(0,100,0,64/BYTES);
  send_aw(0,101,0,64/BYTES);inline_payload(0,0,8'he1);
  for(int n=0;n<3000;n++) begin
    @(negedge clk);if(dut.datapath.channels[0].channel.inline_ecc.data_committed_q) break;
  end
  check(dut.datapath.channels[0].channel.inline_ecc.data_committed_q,"observed scrub data/check interval");
  $display("INLINE_QUIESCE_REQUEST ps=%0d",observer.timestamp_ps());
  write_csr(INDEX_CONTROL,2);
  for(int n=0;n<100;n++) begin @(negedge clk);if(!enable) break;end
  check(!enable && !settled,"quiesce must retain held source obligations");
  repeat(20) begin
    @(negedge clk);check(!settled && status_value[0].state!=1,"quiesce retired held ECC responses");
  end
  source_negative(0);rready[0]=1;
  for(int n=0;n<64/BYTES;n++) begin
    do source_positive(0);while(!rvalid[0]);
    check(r[0].id==100 && r[0].response==0 && r[0].last==(n==64/BYTES-1),"older corrected R identity");
    for(int j=0;j<BYTES;j++) check(r[0].data[j*8+:8]==saved[n*BYTES+j],"older correction snapshot");
    source_negative(0);
  end
  rready[0]=0;drive_bready(1);wait_b(0,101,before_b+1);wait_settled();
  for(int c=0;c<CHANNELS;c++) begin
    for(int n=0;n<3000;n++) begin @(negedge clk);if(status_value[c].state==1 && status_value[c].drained) break;end
    check(status_value[c].state==1 && status_value[c].drained && status_value[c].ecc_drained,"quiesced ECC lock/check drain");
  end
  for(int n=0;n<4;n++) inline_image("quiesced",0,128*n);
  write_csr(INDEX_CONTROL,1);wait(enable);
  send_ar(0,102,0,64/BYTES);read_result(0,102,0,64/BYTES);wait_settled();
  // Keep an already exposed successful B stable while another channel loses
  // ownership between physical data and check commit.
  drive_bready(0);before_b=b_count[1];send_aw(1,108,64,64/BYTES);inline_payload(1,64,8'h69);
  for(int n=0;n<3000;n++) begin source_negative(1);if(bvalid[1]) break;end
  check(bvalid[1] && b[1].response==0,"offered B before dirty ownership fault");
  before_failed_b=b_count[0];send_aw(0,110,512,64/BYTES);inline_payload(0,512,8'hf3);
  for(int n=0;n<3000;n++) begin
    @(negedge clk);if(dut.datapath.channels[0].channel.inline_ecc.data_committed_q) break;
  end
  check(dut.datapath.channels[0].channel.inline_ecc.data_committed_q,"dirty pair before PHY error");
  management[0].error=1;$display("INLINE_PHY_ERROR ps=%0d",observer.timestamp_ps());
  for(int n=0;n<100;n++) begin @(negedge clk);if(host_fault && !enable) break;end
  check(host_fault && !enable && !settled,"actual PHY error fences dirty ECC work");
  write_csr(INDEX_CONTROL,8);
  repeat(20) begin
    @(negedge clk);check(!reset_request && bvalid[1] && b[1].response==0,"reset/response before source settlement");
  end
  drive_bready(1);wait_b(1,108,before_b+1);wait_b(0,110,before_failed_b+1,2);wait_settled();
  for(int n=0;n<100;n++) begin @(negedge clk);if(reset_request) break;end
  check(reset_request,"dirty ECC settlement permits destructive reset request");
  repeat(8) begin @(negedge clk);check(reset_request && status_value[0].boot_epoch==0,"stale reset confirmation");end
  inline_image("torn",0,512);inline_image("held_success",1,64);
  @(negedge clk);finish_observations=1;repeat(2) @(negedge clk);
  management[0].error=0;reset_confirmed=0;memory_reset=1;
  repeat(4) @(negedge clk);reset_confirmed='1;
  for(int n=0;n<300;n++) begin @(negedge clk);if(!host_fault && status_value[0].boot_epoch==1) break;end
  check(!host_fault && status_value[0].boot_epoch==1 && status_value[1].boot_epoch==1
    && !enable && !dut.host_config_active && !status_value[0].config_valid,"dirty destructive epoch invalidation");
  memory_reset=0;for(int i=0;i<65536;i++) expected[i]=0;
  wait_status(INDEX_BOOT_EPOCH,32'hffffffff,1);
  repeat(30) @(negedge pclk);access(0,18'(REG_OFFSET[INDEX_TRAIN_DONE]),0);check(read_data==0,"fresh training required");
  publish_and_boot(1);stage=8;observe_recovery=1;
  before_b=b_count[0];send_aw(0,120,0,64/BYTES);inline_payload(0,0,8'ha7);wait_b(0,120,before_b+1);
  send_ar(1,121,0,64/BYTES);read_result(1,121,0,64/BYTES);wait_settled();inline_image("recovered",0,0);
  @(negedge clk);finish_recovery=1;repeat(2) @(negedge clk);
  check(!host_fault && dut.stale_completions==0,"fresh protected service after dirty recovery");
  $display("CONTROLLER_INLINE_LIFECYCLE_PASS shared_granules=4 scrub=1 phy_error=1 boot=1");
  $display("CONTROLLER_INTEGRATION_PASS checks=%0d inline_lifecycle=1",checks);continuous_close();$finish;
endtask
