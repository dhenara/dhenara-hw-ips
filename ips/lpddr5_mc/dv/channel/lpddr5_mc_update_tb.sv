module lpddr5_mc_update_tb;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0,rst_n=0,reset_update=0,request=0,drained=1,wck_disabled=1,ack=0,phy_request=0;
  logic [1:0] phy_type=0;
  logic [31:0] budget=3000;
  logic controller_request,phy_ack,busy,done,fault;
  lpddr5_mc_profile_t profile;
  string vectors;
  lpddr5_mc_update dut(.clk_ck_i(clk),.rst_ni(rst_n),.destructive_reset_i(reset_update),.profile_i(profile),
    .controller_request_i(request),.drained_i(drained),.wck_disabled_i(wck_disabled),.refresh_budget_i(budget),
    .controller_ack_i(ack),.phy_request_i(phy_request),.phy_type_i(phy_type),.controller_request_o(controller_request),
    .phy_ack_o(phy_ack),.busy_o(busy),.done_o(done),.fault_o(fault));
  task automatic tick; #1; clk=1; #1; clk=0; #1; endtask
  task automatic reset;
    rst_n=0; tick(); rst_n=1; request=0; ack=0; phy_request=0; phy_type=0; drained=1; wck_disabled=1; budget=3000;
  endtask
  initial begin
    int fd,count,assertion_edge,deassertion_edge,release_edge,expected_fault,seen_release,cases;
    profile=fixture_profile(0,0,0,0,0,0);
    if (!$value$plusargs("vectors=%s",vectors)) $fatal(1,"missing vectors");
    fd=$fopen({vectors,"/updates.txt"},"r"); assert(fd!=0) else $fatal(1,"update vectors"); cases=0;
    while (!$feof(fd)) begin
      count=$fscanf(fd,"%d %d %d %d\n",assertion_edge,deassertion_edge,release_edge,expected_fault);
      assert(count==4) else $fatal(1,"update parse");
      reset(); request=1; ack=assertion_edge==0; tick(); request=0; seen_release=-1;
      for (int edge_value=1;edge_value<40;edge_value++) begin
        ack=assertion_edge>=0 && edge_value>=assertion_edge && edge_value<deassertion_edge;
        tick();
        if (done) begin assert(seen_release<0) else $fatal(1,"duplicate release"); seen_release=edge_value; end
      end
      assert(fault==1'(expected_fault)) else $fatal(1,"update fault a=%0d b=%0d",assertion_edge,deassertion_edge);
      if (expected_fault==0) assert(seen_release==release_edge && !busy && !controller_request) else $fatal(1,"release a=%0d b=%0d observed=%0d expected=%0d",assertion_edge,deassertion_edge,seen_release,release_edge);
      cases++;
    end
    $fclose(fd);
    reset(); request=1; drained=0; tick(); request=0;
    assert(!controller_request && !busy && !fault) else $fatal(1,"undrained admission");
    drained=1; budget=33; request=1; tick(); request=0;
    assert(!controller_request && !busy && !fault) else $fatal(1,"refresh admission");
    // A competing PHY request waits for registered controller release.
    reset(); request=1; tick(); request=0; phy_request=1; phy_type=2;
    repeat(4) begin tick(); assert(!phy_ack) else $fatal(1,"overlapping update owners"); end
    assert(!controller_request && done) else $fatal(1,"no-response release");
    tick(); assert(phy_ack && busy) else $fatal(1,"pending PHY update not served");
    repeat(5) tick(); phy_request=0; tick();
    assert(!phy_ack && done && !busy && !fault) else $fatal(1,"PHY return");
    // A stuck PHY request must not be accepted as recovered ownership.
    reset(); phy_request=1; tick(); tick();
    repeat(34) tick(); assert(fault && phy_ack && busy) else $fatal(1,"PHY timeout");
    reset_update=1; tick(); reset_update=0; phy_request=0; tick();
    assert(!fault && !busy && !controller_request && !phy_ack) else $fatal(1,"destructive reset");
    $display("PASS DFI updates: %0d independent sampled-edge traces, drain/budget denial, pending PHY arbitration, stuck fault/reset",cases); $finish;
  end
endmodule
