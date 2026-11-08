`timescale 1ns/1ps
/* verilator lint_off BLKSEQ */
// Real host assembly, independent byte memory, and actual source handshakes.
module lpddr5_mc_host_assembly_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_host_pkg::*;
  /* verilator lint_off SYNCASYNCNET */
  logic clk=0, rst_n=0, enable=0, fatal=0, recover=0, cfg_valid=0, cfg_ready;
  /* verilator lint_on SYNCASYNCNET */
  logic rearm=0, rearm_ready, initialized, config_valid, fault, settled, late_error;
  logic clk0=0, clk1=0, run0=1, run1=1, run_host=1;
  wire [1:0] axi_clk={clk1,clk0};
  logic [1:0] axi_rst_n=0;
  logic [1:0] awvalid=0, arvalid=0, wvalid=0, awready, arready, wready;
  logic [1:0] bvalid, rvalid, bready=3, rready=0;
  lpddr5_mc_axi_address_t [1:0] aw, ar;
  lpddr5_mc_axi_write_t [1:0] wd;
  lpddr5_mc_axi_b_t [1:0] b;
  lpddr5_mc_axi_read_t [1:0] r;
  lpddr5_mc_host_config_t cfg;
  logic [1:0] request_valid, request_ready, completion_valid=0, completion_ready;
  logic [1:0] feedback_valid, feedback_ready=3;
  lpddr5_mc_granule_t [1:0] request;
  lpddr5_mc_host_completion_t [1:0] completion;
  lpddr5_mc_host_feedback_t [1:0] feedback;
  logic [1:0][31:0] boot_epoch, config_epoch;
  logic [31:0] stale;
  logic [63:0] accepted_addresses, grant_cycles;
  logic [7:0] memory[65536], expected[65536];
  logic [1:0] busy=0, physical_sent=0;
  lpddr5_mc_granule_t [1:0] pending;
  logic [1:0][31:0] pending_boot, pending_config;
  int delay_count[2];
  int requests=0, b_count[2], r_count[2], tests=0, stage=0;
  int reported_stage=-1;
  always @(negedge clk) if(stage!=reported_stage) begin
    reported_stage=stage;$display("ASSEMBLY_STAGE stage=%0d at=%0t",stage,$time);$fflush();
  end
  always #0.625 if(run_host) clk=~clk;
  always #1.25 if(run0) clk0=~clk0;
  initial begin #0.375; forever #1.75 if(run1) clk1=~clk1; end

  lpddr5_mc_host #(.DEPTH(16)) dut (
    .clk_i(clk),.rst_ni(rst_n),.cold_rst_ni(rst_n || (|axi_rst_n)),.enable_i(enable),.fatal_i(fatal),.recover_i(recover),
    .cfg_valid_i(cfg_valid),.cfg_ready_o(cfg_ready),.cfg_i(cfg),.rearm_i(rearm),
    .rearm_ready_o(rearm_ready),.initialized_o(initialized),.config_valid_o(config_valid),
    .fault_o(fault),.host_settled_o(settled),.late_error_o(late_error),.stale_completions_o(stale),
    .axi_clk_i(axi_clk),.axi_rst_ni(axi_rst_n),.awvalid_i(awvalid),.arvalid_i(arvalid),.wvalid_i(wvalid),
    .awready_o(awready),.arready_o(arready),.wready_o(wready),.aw_i(aw),.ar_i(ar),.w_i(wd),
    .bvalid_o(bvalid),.rvalid_o(rvalid),.bready_i(bready),.rready_i(rready),.b_o(b),.r_o(r),
    .request_valid_o(request_valid),.request_ready_i(request_ready),.request_o(request),
    .request_boot_epoch_o(boot_epoch),.request_config_epoch_o(config_epoch),
    .completion_valid_i(completion_valid),.completion_ready_o(completion_ready),.completion_i(completion),
    .feedback_valid_o(feedback_valid),.feedback_ready_i(feedback_ready),.feedback_o(feedback),
    .accepted_addresses_o(accepted_addresses),.grant_cycles_o(grant_cycles)
  );
  assign request_ready=~busy & {2{rst_n && !fatal}};
  for(genvar p=0;p<2;p++) begin:monitors
    always @(posedge axi_clk[p]) if(axi_rst_n[p]) begin
      if(bvalid[p]&&bready[p]) b_count[p]++;
      if(rvalid[p]&&rready[p]) r_count[p]++;
    end
    assert property(@(posedge axi_clk[p]) disable iff(!axi_rst_n[p])
      bvalid[p]&&!bready[p] |=> bvalid[p]&&$stable(b[p]))
      else $fatal(1,"B stability p=%0d stage=%0d",p,stage);
    assert property(@(posedge axi_clk[p]) disable iff(!axi_rst_n[p])
      rvalid[p]&&!rready[p] |=> rvalid[p]&&$stable(r[p]));
  end
  // Physical and integrity stages are deliberately separated. This model owns
  // only byte effects and completion witnesses; it is not a DRAM/ECC model.
  always @(posedge clk) begin
    for(int c=0;c<2;c++) begin
      if(!rst_n || fatal) busy[c]=0;
      else begin
        if(completion_valid[c]&&completion_ready[c]&&completion[c].integrity_done) busy[c]=0;
        if(request_valid[c]&&request_ready[c]) begin
          assert(!busy[c]) else $fatal(1,"channel overwrite");
          pending[c]=request[c];pending_boot[c]=boot_epoch[c];pending_config[c]=config_epoch[c];
          physical_sent[c]=0;busy[c]=1;delay_count[c]=3;requests++;
        end
      end
    end
  end
  always @(negedge clk) begin
    completion_valid=0;
    for(int c=0;c<2;c++) if(busy[c] && rst_n && !fatal) begin
      if(delay_count[c]>0) delay_count[c]--;
      else begin
        completion[c]='0;
        completion[c].identity.tag=pending[c].tag;
        completion[c].identity.generation=pending[c].generation;
        completion[c].identity.boot_epoch=pending_boot[c];
        completion[c].identity.config_epoch=pending_config[c];
        completion[c].is_write=pending[c].is_write;
        completion[c].physical_done=1;
        completion[c].integrity_done=physical_sent[c];
        for(int j=0;j<64;j++) begin
          int at;
          at=int'(pending[c].address)+j;
          assert(at<65536)else $fatal(1,"memory bounds");
          if(!physical_sent[c]&&pending[c].is_write&&pending[c].byte_enable[j]) memory[at]=pending[c].data[j*8+:8];
          completion[c].data[j*8+:8]=memory[at];
        end
        completion_valid[c]=1;physical_sent[c]=1;delay_count[c]=3;
      end
    end
  end
  function automatic lpddr5_mc_axi_address_t address(input int id,at,beats);
    lpddr5_mc_axi_address_t a;
    a='0;a.id=16'(id);a.address=40'(at);a.len=8'(beats-1);a.size=4;a.burst=1;
    return a;
  endfunction
  task automatic source_negedge(input int p);
    if(p==0)@(negedge clk0);else @(negedge clk1);
  endtask
  task automatic source_posedge(input int p);
    if(p==0)@(posedge clk0);else @(posedge clk1);
  endtask
  task automatic send_aw(input int p,id,at,beats);
    source_negedge(p);aw[p]=address(id,at,beats);awvalid[p]=1;
    do source_posedge(p);while(!awready[p]);
    source_negedge(p);awvalid[p]=0;
  endtask
  task automatic send_ar(input int p,id,at,beats);
    source_negedge(p);ar[p]=address(id,at,beats);arvalid[p]=1;
    do source_posedge(p);while(!arready[p]);
    source_negedge(p);arvalid[p]=0;
  endtask
  task automatic send_w(input int p,at,beats,start=0);
    lpddr5_mc_axi_write_t value;
    source_negedge(p);wvalid[p]=1;
    for(int n=start;n<beats;n++) begin
      value='0;value.strobe=32'hffff;value.last=n==beats-1;
      for(int j=0;j<16;j++) begin
        value.data[j*8+:8]=8'((at+n*16+j)^((at+n*16+j)>>8)^90);
        expected[at+n*16+j]=value.data[j*8+:8];
      end
      wd[p]=value;
      do source_posedge(p);while(!wready[p]);
      source_negedge(p);
    end
    wvalid[p]=0;
  endtask
  task automatic read_result(input int p,id,at,beats,response=0,start=0);
    source_negedge(p);rready[p]=1;
    for(int n=start;n<beats;n++) begin
      do source_posedge(p);while(!rvalid[p]);
      assert(r[p].id==16'(id)&&r[p].last==(n==beats-1)&&r[p].response==2'(response))
        else $fatal(1,"read metadata p=%0d beat=%0d gotid=%0d resp=%0d",p,n,r[p].id,r[p].response);
      for(int j=0;j<16;j++)
        assert(r[p].data[j*8+:8]==(response==0?expected[at+n*16+j]:8'd0))
          else $fatal(1,"read byte p=%0d at=%0d got=%0x expected=%0x",p,at+n*16+j,r[p].data[j*8+:8],expected[at+n*16+j]);
      source_negedge(p);
    end
    rready[p]=0;
  endtask
  task automatic wait_settled;
    // Callers may have just changed enable/reset at this same edge. Sample
    // after the combinational admission fence has observed those inputs.
    do @(negedge clk);while(!settled);
    repeat(8)@(negedge clk);
    if(!settled)$display("SETTLE_FAIL t=%0t core=%b bridge=%b abort=%b faults=%b/%b eps=%b epabort=%b init=%b counts=%0d/%0d active=%b/%b sourcefault=%b/%b enable=%b busy=%b state=%0d",$time,
      dut.core_settled,dut.bridge_settled,dut.abort_settled,dut.core_fault,dut.bridge_fault,
      dut.bridge.endpoint_settled,dut.bridge.endpoint_abort_settled,initialized,
      dut.bridge.ports[0].endpoint.write_count,dut.bridge.ports[1].endpoint.write_count,
      dut.bridge.ports[0].endpoint.b_active_q,dut.bridge.ports[1].endpoint.b_active_q,
      dut.bridge.ports[0].endpoint.fault_q,dut.bridge.ports[1].endpoint.fault_q,enable,dut.admission_busy,dut.bridge.state_q);
    assert(settled)else $fatal(1,"unstable settlement");
  endtask
  task automatic publish;
    while(!cfg_ready)@(negedge clk);
    cfg_valid=1;@(negedge clk);cfg_valid=0;
    assert(config_valid)else $fatal(1,"config rejected");
    while(!rearm_ready)@(negedge clk);
    rearm=1;@(negedge clk);rearm=0;
    while(!initialized)@(negedge clk);
    enable=1;
  endtask
  task automatic recover_host;
    enable=0;wait_settled();fatal=0;
    @(negedge clk);recover=1;@(negedge clk);recover=0;
    assert(!dut.core_fault)else $fatal(1,"core recovery failed");
    cfg.boot_epoch++;cfg.config_epoch++;publish();
  endtask
  task automatic partial_rearm_reset;
    int before_b;
    while(!cfg_ready) @(negedge clk);
    cfg_valid=1;@(negedge clk);cfg_valid=0;
    while(!rearm_ready) @(negedge clk);
    // The second port cannot install its epoch or acknowledge initialization.
    @(negedge clk1);run1=0;
    rearm=1;@(negedge clk);rearm=0;
    for(int n=0;n<100;n++) begin
      @(negedge clk);
      if(dut.bridge.ports[0].endpoint.ever_epoch_q) break;
    end
    assert(dut.bridge.ports[0].endpoint.ever_epoch_q
      && !dut.bridge.ports[1].endpoint.ever_epoch_q && !initialized)
      else $fatal(1,"partial rearm stimulus did not install exactly one source epoch");
    @(negedge clk);rst_n=0;#2.125;rst_n=1;run1=1;
    repeat(60) @(negedge clk);
    assert(fault && !enable) else $fatal(1,"raw reset during partial rearm lost epoch fault");
    recover_host();
    before_b=b_count[0];send_aw(0,31,28672,4);send_w(0,28672,4);
    while(b_count[0]==before_b) @(negedge clk0);
    send_ar(1,32,28672,4);read_result(1,32,28672,4);wait_settled();
    assert(!fault && initialized && cfg.boot_epoch==2) else $fatal(1,"partial rearm fresh recovery failed");
    $display("HOST_PARTIAL_REARM_RESET_PASS boot=%0d requests=%0d",cfg.boot_epoch,requests);$finish;
  endtask
  initial begin
    lpddr5_mc_axi_b_t held;
    int before_b,before_requests;
    aw='0;ar='0;wd='0;cfg='0;completion='0;pending='0;pending_boot=0;pending_config=0;
    for(int i=0;i<65536;i++)begin memory[i]=0;expected[i]=0;end
    for(int p=0;p<2;p++)begin b_count[p]=0;r_count[p]=0;end
    cfg.boot_epoch=1;cfg.config_epoch=1;cfg.granule64=1;
    cfg.read_limit='{default:16};cfg.write_limit='{default:16};
    cfg.regions[0].enable=1;cfg.regions[0].size=1<<20;
    for(int i=0;i<40;i++)cfg.regions[0].permutation[i]=6'(i);
    #20;rst_n=1;axi_rst_n=3;
    if($test$plusargs("partial_rearm_reset")) partial_rearm_reset();
    publish();stage=1;
    before_b=b_count[0];before_requests=requests;
    send_aw(0,1,4096,128);send_w(0,4096,128);
    while(b_count[0]==before_b)@(negedge clk0);wait_settled();
    assert(requests-before_requests==32)else $fatal(1,"long write splitter count");
    send_ar(1,2,4096,128);read_result(1,2,4096,128);wait_settled();tests++;
    // AW is accepted first; missing W cannot permit a younger overlap read.
    send_aw(0,3,8192,4);
    fork
      begin repeat(120)@(negedge clk0);assert(!rvalid[1])else $fatal(1,"AW hazard lost before W");send_w(0,8192,4);end
      begin send_ar(1,4,8192,4);read_result(1,4,8192,4);end
    join
    wait_settled();tests++;stage=2;
    // Keep the actual source clock stopped while a clean OKAY crosses into
    // return storage. Abort before any source BVALID may become visible.
    bready[0]=0;send_aw(0,5,12288,4);send_w(0,12288,4);
    source_negedge(0);run0=0;
    while(!(dut.b_valid[0]&&dut.b_ready[0]))@(negedge clk);
    @(negedge clk);fatal=1;
    repeat(8)@(negedge clk);assert(!settled)else $fatal(1,"stopped source falsely settled");
    run0=1;while(!bvalid[0])@(negedge clk0);
    assert(b[0].id==5&&b[0].response==2)else $fatal(1,"queued OKAY exposed after abort");
    source_negedge(0);before_b=b_count[0];bready[0]=1;while(b_count[0]==before_b)@(negedge clk0);
    recover_host();tests++;stage=3;
    // Actual B may handshake while its exposure/handshake feedback has not
    // reached a stopped common clock. The surviving ledger owns late errors.
    assert(!late_error)else $fatal(1,"unexposed error incorrectly reported late");
    bready[0]=0;send_aw(0,11,14336,4);send_w(0,14336,4);
    while(!bvalid[0])@(negedge clk0);
    @(negedge clk);run_host=0;
    source_negedge(0);before_b=b_count[0];bready[0]=1;while(b_count[0]==before_b)@(negedge clk0);
    fatal=1;repeat(8)@(negedge clk0);
    assert(late_error)else $fatal(1,"consumed OKAY lost late fault during feedback crossing");
    run_host=1;recover_host();tests++;
    bready[0]=0;send_aw(0,6,16384,4);send_w(0,16384,4);
    while(!bvalid[0])@(negedge clk0);held=b[0];assert(held.response==0)else $fatal(1,"clean B failed");
    fatal=1;repeat(20)@(negedge clk0);
    assert(bvalid[0]&&b[0]==held&&late_error&&!settled)else $fatal(1,"exposed B abort stability/late error");
    before_b=b_count[0];bready[0]=1;while(b_count[0]==before_b)@(negedge clk0);
    recover_host();tests++;stage=4;
    // A peer AXI reset may discard that peer's ledger, but port zero survives.
    send_aw(0,7,20480,4);send_w(0,20480,2);
    @(negedge clk);axi_rst_n[1]=0;repeat(8)@(negedge clk);axi_rst_n[1]=1;
    send_w(0,20480,4,2);wait_settled();assert(fault)else $fatal(1,"peer reset did not fault");
    recover_host();send_ar(0,8,4096,4);read_result(0,8,4096,4);wait_settled();tests++;stage=5;
    // Core reset flushes transport while the source still owes W and B.
    send_aw(0,9,24576,4);send_w(0,24576,2);
    @(negedge clk);enable=0;rst_n=0;repeat(8)@(negedge clk);rst_n=1;
    send_w(0,24576,4,2);wait_settled();
    assert(fault)else $fatal(1,"core reset lost fault observation");
    recover_host();
    send_ar(1,10,4096,4);read_result(1,10,4096,4);wait_settled();tests++;
    assert(tests==7&&stale==0)else $fatal(1,"assembly checks/count");
    $display("HOST_ASSEMBLY_PASS tests=%0d requests=%0d B=%0d/%0d Rbeats=%0d/%0d",tests,requests,b_count[0],b_count[1],r_count[0],r_count[1]);
    $finish;
  end
  initial begin
    #20000;
    $display("ASSEMBLY_DIAG enable=%b fatal=%b recover=%b cfgvalid=%b cfgready=%b rearmready=%b corefault=%b bridgefault=%b abort=%b qreq=%b qack=%b sourcefault=%b/%b sourceidle=%b sourcecounts=%0d/%0d",
      enable,fatal,recover,config_valid,cfg_ready,rearm_ready,dut.core_fault,dut.bridge_fault,dut.abort_settled,
      dut.bridge.quiesce_request_q,dut.bridge.endpoint_quiesce_ack,
      dut.bridge.ports[0].endpoint.fault_q,dut.bridge.ports[1].endpoint.fault_q,
      dut.bridge.endpoint_admission_idle,dut.bridge.ports[0].endpoint.write_count,dut.bridge.ports[0].endpoint.read_count);
    $fatal(1,"assembly timeout stage=%0d fault=%b settled=%b core=%b bridge=%b initialized=%b busy=%b",stage,fault,settled,dut.core_settled,dut.bridge_settled,initialized,dut.admission_busy);
  end
endmodule
