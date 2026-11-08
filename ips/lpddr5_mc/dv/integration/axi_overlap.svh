// Exercise accepted same-port hazards and cross-port ownership turnover.
// Cross-port conflicts are intentionally blocked before READY by the bridge;
// holding a competing VALID must not steal an AW's rights while W is delayed.
task automatic overlap_case;
  int before_b;
  longint unsigned at;
  bit allow_competing_accept;
  check(PORTS==2 && INTEGRITY_MODE==0,"overlap fixture configuration");
  for(int turn=0;turn<4;turn++) begin
    int p,other;
    p=turn%2;other=1-p;at=64'd32768+64'(turn)*64;
    stage=200+turn;before_b=b_count[p];allow_competing_accept=0;
    source_negative(p);bready[p]=0;rready[p]=0;
    send_aw(p,3,at,64/BYTES);
    // These same-ID reads must see the first write, even though another
    // overlapping write is fully buffered before their R handshakes.
    send_ar(p,3,at,64/BYTES);send_ar(p,3,at,64/BYTES);
    send_aw(p,3,at,64/BYTES);
    fork
      begin
        source_negative(other);ar[other]=address(3,at,64/BYTES);arvalid[other]=1;
        do begin
          source_positive(other);
          check(!arready[other] || allow_competing_accept,"conflicting port accepted before old host retirement");
        end while(!arready[other]);
        source_negative(other);arvalid[other]=0;
        read_result(other,3,at,64/BYTES);
      end
      begin
        repeat(32) source_negative(p);
        check(!bvalid[p] && !rvalid[p],"AW hazard protects reads before W validation");
        send_w(p,at,64/BYTES);
        send_w(.p(p),.at(at),.beats(64/BYTES),.salt(8'hff),.update_expected(0));
        while(!rvalid[p]) source_negative(p);
        repeat(8) source_negative(p);
        read_result(p,3,at,64/BYTES);read_result(p,3,at,64/BYTES);
        // Change only the source expectation for the later read snapshot.
        for(int j=0;j<64;j++) expected[at+64'(j)]^=8'hff;
        while(!bvalid[p]) source_negative(p);
        repeat(8) source_negative(p);
        allow_competing_accept=1;bready[p]=1;
        wait_b(p,3,before_b+2);
      end
    join
    wait_settled();
  end
  // Every response slot remains reserved through the actual source handshake.
  // Establish bucket rights before filling a port, avoiding a handover that
  // would correctly wait for a different port's held responses.
  for(int p=0;p<PORTS;p++) begin
    at=64'd65536+64'(p)*2048;before_b=b_count[p];stage=210+p;
    source_negative(p);bready[p]=0;
    for(int n=0;n<DEPTH;n++) begin
      send_aw(p,5,at+64'(n)*64,64/BYTES);send_w(p,at+64'(n)*64,64/BYTES);
    end
    while(!bvalid[p]) source_negative(p);
    aw[p]=address(5,at+64'(DEPTH)*64,64/BYTES);awvalid[p]=1;
    repeat(32) begin source_positive(p);check(!awready[p],"write response capacity must block next AW");source_negative(p);end
    bready[p]=1;
    do source_positive(p);while(!awready[p]);source_negative(p);awvalid[p]=0;
    send_w(p,at+64'(DEPTH)*64,64/BYTES);wait_b(p,5,before_b+DEPTH+1);wait_settled();
    source_negative(p);rready[p]=0;
    for(int n=0;n<DEPTH;n++) send_ar(p,6,at+64'(n)*64,64/BYTES);
    while(!rvalid[p]) source_negative(p);
    ar[p]=address(6,at+64'(DEPTH)*64,64/BYTES);arvalid[p]=1;
    repeat(32) begin source_positive(p);check(!arready[p],"read response capacity must block next AR");source_negative(p);end
    fork
      begin
        do source_positive(p);while(!arready[p]);source_negative(p);arvalid[p]=0;
      end
      begin
        for(int n=0;n<=DEPTH;n++) read_result(p,6,at+64'(n)*64,64/BYTES);
      end
    join
    wait_settled();
  end
  check(!host_fault && stale==0,"overlap and credit pressure remain healthy");
  @(negedge clk);finish_observations=1;repeat(2) @(negedge clk);
  $display("DATAPATH_OVERLAP_PASS turns=4 depth=%0d",DEPTH);
  $display("DATAPATH_INTEGRATION_PASS checks=%0d overlap=1",checks);$finish;
endtask
