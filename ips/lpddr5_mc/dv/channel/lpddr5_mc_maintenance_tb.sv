module lpddr5_mc_maintenance_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0,rst_n=0,reset_maintenance=0,start=0,per_bank=0,temperature_valid=0,sr_request=0;
  logic [4:0] temperature=9,thermal;
  logic [1:0] sr_enter=0,sr_exit=0,external_refresh=0;
  logic request_valid,request_ready=0,request_rank,request_rfm,request_all;
  logic [2:0] request_pair;
  logic [1:0][7:0] allowed;
  logic urgent,fault,extra,effective_per_bank,mr4_request,zq_request,mr4_done=0,zq_done=0;
  logic [31:0] budget,diagnostic;
  lpddr5_mc_profile_t profile,effective;
  lpddr5_mc_event_t event_value;
  logic unused_debug;
  assign unused_debug=^{diagnostic,zq_request,effective};
  lpddr5_mc_maintenance dut(.clk_ck_i(clk),.rst_ni(rst_n),.destructive_reset_i(reset_maintenance),.start_i(start),.profile_i(profile),.per_bank_i(per_bank),
    .pins_idle_i(1'b0),
    .event_i(event_value),.temperature_valid_i(temperature_valid),.temperature_i(temperature),.self_refresh_enter_i(sr_enter),.self_refresh_exit_i(sr_exit),
    .self_refresh_request_i(sr_request),.request_valid_o(request_valid),.request_ready_i(request_ready),.request_rank_o(request_rank),.request_rfm_o(request_rfm),
    .request_all_o(request_all),.request_pair_o(request_pair),.activate_allowed_o(allowed),.urgent_o(urgent),.fault_o(fault),.extra_pending_o(extra),
    .refresh_budget_o(budget),.effective_per_bank_o(effective_per_bank),.effective_profile_o(effective),.mr4_request_o(mr4_request),.zq_request_o(zq_request),.mr4_done_i(mr4_done),.zq_done_i(zq_done),
    .thermal_o(thermal),.diagnostic_o(diagnostic),.external_refresh_i(external_refresh));
  task automatic tick; #1; clk=1; #1; clk=0; #1; endtask
  task automatic reset;
    rst_n=0; tick(); rst_n=1; event_value='0; sr_enter=0; sr_exit=0; external_refresh=0;sr_request=0; temperature_valid=0;
    per_bank=0; request_ready=0; start=1; mr4_done=0; zq_done=0; tick(); start=0;
  endtask
  task automatic wait_request;
    for(int j=0;j<3200;j++) begin
      #1; if(request_valid) return;
      if(mr4_request) mr4_done=1; else mr4_done=0;
      tick(); assert(!fault) else $fatal(1,"maintenance before request");
    end
    $fatal(1,"missing maintenance request");
  endtask
  initial begin
    logic held_rank,held_rfm,held_all;
    logic [2:0] held_pair;
    profile=fixture_profile(0,0,0,0,0,0); profile.ranks=1; event_value='0;
    reset(); assert(allowed[1]==0) else $fatal(1,"inactive rank");
    // Counter saturation cannot be cleared by an ordinary self-refresh cycle.
    repeat(64) begin event_value.row_opened=1; event_value.bank=0; tick(); end
    event_value='0; assert(!allowed[0][0] && urgent && !fault) else $fatal(1,"paired RAA maximum");
    wait_request(); assert(request_rank==0 && request_rfm && request_all) else $fatal(1,"mandatory RFM request");
    request_ready=1; tick(); request_ready=0; tick();
    assert(allowed[0][0] && !fault) else $fatal(1,"RFM decrement");
    reset(); per_bank=1; wait_request();
    for(int p=0;p<8;p++) begin
      wait_request();
      assert(request_rank==0 && !request_rfm && !request_all && request_pair==3'(p)) else $fatal(1,"REFpb sequence");
      held_rank=request_rank; held_rfm=request_rfm; held_all=request_all; held_pair=request_pair;
      repeat(3) begin tick(); assert(request_valid && {request_rank,request_rfm,request_all,request_pair}=={held_rank,held_rfm,held_all,held_pair}) else $fatal(1,"unstable maintenance request"); end
      request_ready=1; tick(); request_ready=0;
      if(p<7) assert(budget<3000) else $fatal(1,"partial REFpb round discharged periodic obligation");
    end
    assert(budget>3000 && !fault) else $fatal(1,"full REFpb round");
    // Both ranks retain pair order, but neither rank monopolizes the round.
    profile.ranks=2;reset();per_bank=1;
    for(int p=0;p<8;p++) for(int r=0;r<2;r++) begin
      wait_request();
      assert(effective_per_bank && request_rank==1'(r) && request_pair==3'(p) && !request_all)
        else $fatal(1,"REFpb rank interleave/order");
      request_ready=1;tick();request_ready=0;
    end
    // At 781 CK the minimal pair round fits, but drain plus MR4 service
    // does not. Admission must use the same complete reserve as the channel.
    for(int code_value=12;code_value<=15;code_value++) begin
      reset();per_bank=1;temperature=5'(code_value);temperature_valid=1;tick();temperature_valid=0;
      assert(!effective_per_bank) else $fatal(1,"infeasible hot REFpb round admitted code=%0d",code_value);
      wait_request();assert(request_all && !request_rfm) else $fatal(1,"hot all-bank fallback");
    end
    // A PHY refresh can leave rank deadlines far apart. All-bank service
    // must pull the other populated rank into the same periodic round;
    // otherwise overlapping recovery windows can starve exclusive MR4.
    reset();repeat(500) tick();external_refresh=2;tick();external_refresh=0;
    wait_request();assert(request_rank==0 && request_all) else $fatal(1,"skewed round first rank");
    request_ready=1;tick();request_ready=0;
    repeat(8) begin tick();if(request_valid) break;end
    assert(request_valid && request_rank==1 && request_all && !request_rfm)
      else $fatal(1,"staggered all-bank refresh failed to group ranks");
    request_ready=1;tick();request_ready=0;tick();assert(!urgent && !fault) else $fatal(1,"grouped round settlement");
    profile.ranks=1;
    // SR hold suspends external retention. Exit requires a separate extra REF.
    reset(); sr_enter=1; tick(); sr_enter=0; repeat(4000) tick();
    assert(!fault && !allowed[0][0]) else $fatal(1,"long self-refresh retention");
    sr_exit=1; tick(); sr_exit=0; assert(extra && budget>3000) else $fatal(1,"SRX obligation");
    sr_request=1; wait_request(); assert(request_all && !request_rfm) else $fatal(1,"extra REF");
    request_ready=1; tick(); request_ready=0; sr_request=0; tick(); assert(!extra && !fault) else $fatal(1,"extra refresh settlement");
    temperature_valid=1; temperature=13; tick(); temperature_valid=0;
    assert(thermal==13 && effective.dram_ck_trcd==profile.dram_ck_trcd+2 && effective.dram_ck_trc==profile.dram_ck_trc+3) else $fatal(1,"thermal timing derating");
    temperature_valid=1; temperature=0; tick(); temperature_valid=0;
    assert(fault) else $fatal(1,"reserved temperature");
    reset_maintenance=1; tick(); reset_maintenance=0; tick(); assert(!fault && thermal==9 && !urgent && !extra) else $fatal(1,"volatile recovery state");
    reset(); repeat(3010) tick();
    assert(fault && diagnostic[31:28]==5) else $fatal(1,"missed MR4 watchdog diagnostic");
    reset(); repeat(3200) begin mr4_done=mr4_request; tick(); end
    assert(fault && diagnostic[31:28]==3) else $fatal(1,"missed retention deadline");
    // Repeated valid temperature samples cannot postpone an overdue
    // thermal drain indefinitely.
    reset(); repeat(1000) tick(); temperature=15; temperature_valid=1;
    repeat(1000) tick(); temperature_valid=0;
    assert(fault && diagnostic[31:28]==3) else $fatal(1,"thermal debt resampling extended deadline");
    held_pair=diagnostic[2:0]; reset_maintenance=1; tick(); reset_maintenance=0; tick();
    assert(!fault && diagnostic[2:0]==held_pair && diagnostic!=0) else $fatal(1,"retained first fault diagnostic");
    $display("PASS maintenance: paired RAA/RFM, REFpb full rounds, stable requests, long SR/SRX extra REF, thermal derating/invalidity, retention fault and destructive recovery"); $finish;
  end
endmodule
