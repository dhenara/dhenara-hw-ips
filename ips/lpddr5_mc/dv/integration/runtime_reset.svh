// Exercise the real wrapper's common reset while both AXI ledgers survive.
// The memory fixture explicitly loses its tracked contents at raw reset; no
// preservation claim is inferred from the clean traces around this scenario.
task automatic runtime_reset_run;
  int before_b;
  @(negedge clk);finish_observations=1;repeat(2) @(negedge clk);
  for(int epoch_value=0;epoch_value<2;epoch_value++) begin
    stage=10+epoch_value;
    bready=0;before_b=b_count[0];
    send_aw(0,20,16384,64/BYTES);send_ar(PORTS-1,21,20544,64/BYTES);
    @(negedge clk);rst_n=0;memory_reset=1;
    // Fractional assertion/release checks the actual asynchronous reset path.
    #2.125;rst_n=1;
    repeat(20) @(negedge clk);
    check(host_fault && !enable && !settled,"raw reset must retain fault and accepted obligations");
    check(status_value[0].boot_epoch==32'(epoch_value)
      && status_value[CHANNELS-1].boot_epoch==32'(epoch_value),"raw reset reused an earlier boot epoch");
    send_w(0,16384,64/BYTES);read_result(PORTS-1,21,20544,64/BYTES,2);
    bready='1;wait_b(0,20,before_b+1,2);wait_settled();
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
    // APB staging was also reset. Reload the canonical image before publication.
    for(int regindex=0;regindex<REG_COUNT;regindex++) if(REG_RW[regindex]!=0) begin
      if(REG_SHARED[regindex]) write_csr(regindex,staging_image[regindex]&REG_RW[regindex]);
      else for(int c=0;c<CHANNELS;c++) access(1,18'h10000+18'(c)*18'h10000+18'(REG_OFFSET[regindex]),
        staging_image[regindex]&REG_RW[regindex]);
    end
    publish_and_boot(epoch_value+1);
    if(epoch_value==1) observe_recovery=1;
    before_b=b_count[0];send_aw(0,22,24576,64/BYTES);send_w(0,24576,64/BYTES);wait_b(0,22,before_b+1);
    send_ar(PORTS-1,23,24576,64/BYTES);read_result(PORTS-1,23,24576,64/BYTES);wait_settled();
    check(!host_fault && enable,"fresh traffic failed after raw reset recovery");
    $display("RUNTIME_RESET_RECOVERY_PASS boot=%0d",epoch_value+1);
  end
  @(negedge clk);finish_recovery=1;repeat(2) @(negedge clk);
  $display("CONTROLLER_INTEGRATION_PASS runtime_reset checks=%0d boot=%0d",checks,status_value[0].boot_epoch);
  $finish;
endtask
