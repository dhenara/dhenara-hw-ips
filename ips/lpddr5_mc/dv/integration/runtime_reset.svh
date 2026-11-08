// Exercise the real wrapper's operational resets and surviving AXI ledgers.
// The memory fixture explicitly loses its tracked contents at raw reset; no
// preservation claim is inferred from the clean traces around this scenario.
task automatic runtime_reset_run;
  int before_b,case_count,case_kind,surviving_port,read_port,reset_port;
  bit common_reset;
  case_count=RESET_MATRIX ? 10 : 2;
  @(negedge clk);finish_observations=1;repeat(2) @(negedge clk);
  for(int epoch_value=0;epoch_value<case_count;epoch_value++) begin
    // Cases 0/1 retain repeated common resets. Cases 2/3 reset each AXI
    // endpoint; 4/5 stop CK/APB during common reset; 6/7 stop the reset AXI
    // clock; 8/9 stop a surviving AXI clock during common reset.
    case_kind=RESET_MATRIX ? epoch_value : 0;
    common_reset=!(case_kind inside {2,3,6,7});
    reset_port=(case_kind==2 || case_kind==6) ? 0 : PORTS-1;
    surviving_port=common_reset ? 0 : PORTS-1-reset_port;
    read_port=common_reset ? PORTS-1 : surviving_port;
    stage=10+epoch_value;
    bready=0;before_b=b_count[surviving_port];
    send_aw(surviving_port,20,16384,64/BYTES);send_ar(read_port,21,20544,64/BYTES);
    if(!common_reset) send_ar(reset_port,30,22528,64/BYTES);
    if(case_kind==4) begin @(negedge clk);run_ck=0;end
    if(case_kind==5) begin @(negedge pclk);run_pclk=0;end
    if(case_kind inside {6,7}) begin source_negative(reset_port);run_axi[reset_port]=0;end
    if(case_kind inside {8,9}) begin source_negative(case_kind-8);run_axi[case_kind-8]=0;end
    #0.125;
    if(common_reset) rst_n=0;else axi_rst_n[reset_port]=0;
    memory_reset=1;
    // Fractional assertion/release checks the actual asynchronous reset path.
    #2.125;
    if(common_reset) rst_n=1;else axi_rst_n[reset_port]=1;
    if(case_kind==4) begin #2.125;run_ck=1;end
    repeat(20) @(negedge clk);
    check(host_fault && !enable && !settled,"raw reset must retain fault and accepted obligations");
    check(status_value[0].boot_epoch==32'(epoch_value)
      && status_value[CHANNELS-1].boot_epoch==32'(epoch_value),"raw reset reused an earlier boot epoch");
    run_pclk=1;run_axi='1;
    if(!common_reset) check(!rvalid[reset_port],"reset source retained a stale read response");
    send_w(surviving_port,16384,64/BYTES);read_result(read_port,21,20544,64/BYTES,2);
    bready='1;wait_b(surviving_port,20,before_b+1,2);wait_settled();
    wait_status(INDEX_STATUS,32'h10,32'h10);
    wait_status(INDEX_BOOT_EPOCH,32'hffffffff,32'(epoch_value));
    write_csr(INDEX_CONTROL,8);
    for(int n=0;n<300;n++) begin @(negedge clk);if(reset_request) break;end
    check(reset_request,"raw reset recovery must request confirmed destruction");
    repeat(8) begin
      @(negedge clk);check(reset_request && status_value[0].boot_epoch==32'(epoch_value),
        "stale high destructive confirmation accepted after raw reset");
    end
    reset_confirmed=0;repeat(4) @(negedge clk);reset_confirmed='1;
    for(int n=0;n<300;n++) begin
      @(negedge clk);if(!host_fault && status_value[0].boot_epoch==32'(epoch_value+1)) break;
    end
    check(!host_fault && !enable && !dut.host_config_active
      && status_value[0].boot_epoch==32'(epoch_value+1)
      && status_value[CHANNELS-1].boot_epoch==32'(epoch_value+1),"raw reset destructive recovery failed");
    wait_status(INDEX_BOOT_EPOCH,32'hffffffff,32'(epoch_value+1));
    memory_reset=0;for(int i=0;i<65536;i++) expected[i]=0;
    // Common reset clears APB staging; endpoint-only reset preserves it.
    // Reload the same canonical image in either case before publication.
    for(int regindex=0;regindex<REG_COUNT;regindex++) if(REG_RW[regindex]!=0) begin
      if(REG_SHARED[regindex]) write_csr(regindex,staging_image[regindex]&REG_RW[regindex]);
      else for(int c=0;c<CHANNELS;c++) access(1,18'h10000+18'(c)*18'h10000+18'(REG_OFFSET[regindex]),
        staging_image[regindex]&REG_RW[regindex]);
    end
    publish_and_boot(epoch_value+1);
    if(epoch_value==case_count-1) observe_recovery=1;
    before_b=b_count[0];send_aw(0,22,24576,64/BYTES);send_w(0,24576,64/BYTES);wait_b(0,22,before_b+1);
    send_ar(PORTS-1,23,24576,64/BYTES);read_result(PORTS-1,23,24576,64/BYTES);wait_settled();
    check(!host_fault && enable,"fresh traffic failed after raw reset recovery");
    $display("RUNTIME_RESET_RECOVERY_PASS boot=%0d case=%0d surviving_port=%0d",epoch_value+1,case_kind,surviving_port);
  end
  @(negedge clk);finish_recovery=1;repeat(2) @(negedge clk);
  $display("CONTROLLER_INTEGRATION_PASS runtime_reset checks=%0d boot=%0d",checks,status_value[0].boot_epoch);
  $finish;
endtask
