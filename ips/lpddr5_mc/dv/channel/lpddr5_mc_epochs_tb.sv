module lpddr5_mc_epochs_tb;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0,rst_n=0,reset_epoch=0,loss=0,posted=1;
  lpddr5_mc_profile_t profile;
  logic add_valid=0,add_ready,add_rank=0,physical_valid=0,flush=0;
  lpddr5_mc_channel_identity_t identity;
  lpddr5_mc_channel_completion_t physical,completion;
  logic poll_valid,poll_ready=0,poll_rank,result_valid=0,result_rank=0,result_failed=0,drain_requested;
  logic feedback_valid=0,completion_valid,completion_ready=0,drained,fault,late_error;
  lpddr5_mc_channel_response_feedback_t feedback;
  logic [63:0] stale;
  logic unused_debug;
  assign unused_debug=^{poll_rank,stale,drain_requested};
  lpddr5_mc_write_epochs dut(.clk_ck_i(clk),.rst_ni(rst_n),.destructive_reset_i(reset_epoch),.ownership_loss_i(loss),
    .profile_i(profile),.posted_i(posted),.add_valid_i(add_valid),.add_ready_o(add_ready),.add_rank_i(add_rank),.add_identity_i(identity),
    .physical_valid_i(physical_valid),.physical_i(physical),.flush_i(flush),.drain_reserve_i(32'd84),.drain_requested_o(drain_requested),
    .poll_valid_o(poll_valid),.poll_ready_i(poll_ready),.poll_rank_o(poll_rank),
    .result_valid_i(result_valid),.result_rank_i(result_rank),.result_failed_i(result_failed),.feedback_valid_i(feedback_valid),.feedback_i(feedback),
    .completion_valid_o(completion_valid),.completion_ready_i(completion_ready),.completion_o(completion),.drained_o(drained),.fault_o(fault),
    .late_error_o(late_error),.stale_feedback_o(stale));
  task automatic tick; #1; clk=1; #1; clk=0; #1; endtask
  task automatic reset;
    rst_n=0; tick(); rst_n=1; reset_epoch=0; loss=0; add_valid=0; physical_valid=0; flush=0;
    poll_ready=0; result_valid=0; feedback_valid=0; completion_ready=0; posted=1;
  endtask
  task automatic add_member(input int rank_value,tag_value,generation_value);
    assert(generation_value>=0 && generation_value<=65535) else $fatal(1,"generation range");
    identity='0; identity.tag=64'(tag_value); identity.generation=16'(generation_value); identity.boot_epoch=5; identity.config_epoch=9;
    add_rank=1'(rank_value); add_valid=1; #1;
    assert(add_ready) else $fatal(1,"missing epoch credit rank=%0d tag=%0d",rank_value,tag_value);
    tick(); add_valid=0;
    physical='0; physical.identity=identity; physical.is_write=1; physical.physical_done=1;
    physical_valid=1; tick(); physical_valid=0;
  endtask
  task automatic take(input logic final_value, input logic failed_value);
    int n;
    lpddr5_mc_channel_completion_t held;
    n=0;
    while (!completion_valid && n<50) begin tick(); n++; end
    assert(completion_valid && completion.integrity_done==final_value && completion.failed==failed_value && completion.physical_done)
      else $fatal(1,"wrong completion final=%0d failed=%0d",final_value,failed_value);
    held=completion;
    repeat(final_value ? 3 : 0) begin tick(); assert(completion_valid && completion==held) else $fatal(1,"unstable completion"); end
    feedback='0; feedback.identity=completion.identity; feedback.exposed=1; feedback.handshaken=1; feedback.response=0;
    completion_ready=1; tick(); completion_ready=0;
    if (!final_value) begin feedback_valid=1; tick(); feedback_valid=0; end
  endtask
  task automatic settle(input logic rank_value,failed_value);
    flush=1; tick(); #1; assert(poll_valid && poll_rank==1'(rank_value)) else $fatal(1,"MR43 poll before/after drain");
    poll_ready=1; tick(); poll_ready=0; flush=0;
    result_valid=1; result_rank=1'(rank_value); result_failed=1'(failed_value); tick(); result_valid=0;
  endtask
  initial begin
    identity='0; physical='0; feedback='0; profile=fixture_profile(0,0,1,1,0,0); profile.ranks=2;
    reset();
    // All 32 membership slots are live concurrently. This exceeds a depth16
    // physical queue and proves membership is not one global sixteen limit.
    for(int r=0;r<2;r++) for(int j=0;j<16;j++) begin add_member(r,r*16+j+1,1); take(0,0); end
    #1; assert(!add_ready && poll_valid && !fault) else $fatal(1,"rank epoch limit");
    settle(0,1); repeat(16) take(1,1);
    settle(1,0); repeat(16) take(1,0);
    repeat(40) tick(); assert(drained && late_error && !fault) else $fatal(1,"epoch release/late fault");
    // Reuse tag with newer generation. Old feedback cannot touch new member.
    add_member(0,1,2); take(0,0);
    feedback.identity.generation=1; feedback_valid=1; tick(); feedback_valid=0;
    assert(stale==1 && !fault) else $fatal(1,"stale generation feedback");
    settle(0,0); take(1,0); repeat(40) tick();
    // Strict mode must withhold completion until actual physical drain + MR43.
    reset(); posted=0; add_member(0,2,1); repeat(5) tick();
    assert(!completion_valid) else $fatal(1,"premature strict completion");
    flush=1; tick(); settle(0,1); take(1,1);
    // Ownership loss fails pending members; destructive reset is explicit.
    reset(); add_member(1,3,1); take(0,0); loss=1; tick(); loss=0; take(1,1);
    assert(fault && late_error && !add_ready) else $fatal(1,"ownership loss");
    reset_epoch=1; tick(); reset_epoch=0; tick();
    assert(drained && !fault && late_error) else $fatal(1,"destructive reset/retained diagnostic");
    // A newer rank-zero epoch must not jump an older rank-one deadline when
    // both polls become ready together on the single register transport.
    reset();posted=0;add_member(1,90,1);repeat(40) tick();add_member(0,91,1);
    flush=1;tick();flush=0;#1;
    assert(drain_requested && poll_valid && poll_rank==1 && !fault) else $fatal(1,"oldest MR43 epoch lost priority");
    settle(1,0);take(1,0);settle(0,0);take(1,0);
    $display("PASS MR43: both ranks, 32 live members, posted/strict, stable stalls, generation reuse, late fault, ownership reset"); $finish;
  end
endmodule
