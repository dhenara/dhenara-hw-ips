// CK-rate retention and paired-bank RFM owner. Issued maintenance, not queue
// removal or an elapsed timer, discharges a refresh/counter obligation.
module lpddr5_mc_maintenance (
  input logic clk_ck_i, rst_ni, destructive_reset_i, start_i,
  input logic pins_idle_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_profile_t profile_i,
  input logic per_bank_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_event_t event_i,
  input logic temperature_valid_i,
  input logic [4:0] temperature_i,
  input logic [1:0] self_refresh_enter_i, self_refresh_exit_i,
  input logic [1:0] external_refresh_i,
  input logic self_refresh_request_i,
  output logic request_valid_o,
  input logic request_ready_i,
  output logic request_rank_o, request_rfm_o, request_all_o,
  output logic [2:0] request_pair_o,
  output logic [1:0][7:0] activate_allowed_o,
  output logic urgent_o, fault_o, extra_pending_o,
  output logic [31:0] refresh_budget_o,
  output logic effective_per_bank_o,
  output lpddr5_mc_channel_pkg::lpddr5_mc_profile_t effective_profile_o,
  output logic mr4_request_o, zq_request_o,
  input logic mr4_done_i, zq_done_i,
  output logic [4:0] thermal_o,
  output logic [31:0] diagnostic_o
);
  import lpddr5_mc_channel_pkg::*;
  logic running_q;
  logic [63:0] ck_q, last_refresh_q[2], last_pair_q[2], thermal_deadline_q, last_mr4_q, last_zq_q;
  logic [31:0] raa_q[2][8];
  logic [1:0] self_refresh_q, extra_q, thermal_debt_q, all_bank_round_q;
  logic [2:0] next_pair_q[2];
  logic pending_q, pending_rank_q, pending_rfm_q, pending_all_q, pending_extra_q;
  logic [2:0] pending_pair_q;
  logic [31:0] interval_value, new_interval, imt, maximum, decrement, reserve_value, poll_lead;
  logic candidate, candidate_rank, candidate_rfm, candidate_extra;
  logic periodic_due, rfm_due, all_bank_due;
  logic [63:0] budget_value, candidate_age, rank_age;
  logic unused_profile;
  assign unused_profile=^{profile_i,event_i};
  function automatic logic [31:0] interval_for(input logic [4:0] code_value);
    case (code_value)
      1,2,3,4,5,6,7,8,9: return 3124;
      10: return 2186;
      11: return 1562;
      12,13: return 781;
      14,15: return 390;
      default: return 0;
    endcase
  endfunction
  assign extra_pending_o=|(extra_q & (2'b11>>(2-int'(profile_i.ranks))));
  always_comb begin
    effective_profile_o=profile_i;
    if(thermal_o inside {13,15}) begin
      effective_profile_o.dram_ck_trcd=profile_i.dram_ck_trcd+2;
      effective_profile_o.dram_ck_trp_pb=profile_i.dram_ck_trp_pb+2;
      effective_profile_o.dram_ck_trp_ab=profile_i.dram_ck_trp_ab+2;
      effective_profile_o.dram_ck_tras_min=profile_i.dram_ck_tras_min+2;
      effective_profile_o.dram_ck_trc=profile_i.dram_ck_trc+3;
    end
  end
  always_comb begin
    interval_value=interval_for(thermal_o); new_interval=interval_for(temperature_i);
    // A minimal pair round alone is insufficient: traffic drain and a
    // mandatory register visit must also fit. Otherwise MR4 can shorten
    // the interval while too little time remains to finish that round.
    // Use REFab at such intervals, replacing any partial pair round.
    effective_per_bank_o=per_bank_i && interval_value>
      maintenance_admission_budget(effective_profile_o,1'b1)+register_service_budget(effective_profile_o)+8;
    imt=32'(profile_i.mr27[5:1])*8;
    maximum=imt*32'(2+2*int'(profile_i.mr27[7:6]));
    case(profile_i.mr57[1:0])
      0: decrement=imt;
      1: decrement=imt+imt/2;
      2: decrement=imt*2;
      default: decrement=imt*4;
    endcase
    // Share the thermal/pair-aware reserve with link-epoch admission so a
    // newly started integrity epoch cannot cross the next urgent REF window.
    reserve_value=maintenance_admission_budget(effective_profile_o,effective_per_bank_o);
    // Once the actual backend has closed pages, drained pins and disabled
    // WCK, do not reserve that drain again. At the hottest interval, two
    // ranks need the remaining gaps for per-rank MR4/ZQ service. Keep the
    // full REF recovery/round reserve for serialized maintenance commands.
    // pins_idle_i means rows are closed, including during REF recovery.
    // Requiring recovery completion here would immediately reserve another
    // drain and queue REF again before recovery ends at hot intervals.
    if(pins_idle_i) reserve_value=effective_per_bank_o
      ? 32'(effective_profile_o.dram_ck_trfc_pb)+7*32'(effective_profile_o.dram_ck_tpbr2pbr)+8
      : 32'(effective_profile_o.dram_ck_trfc_ab)+8;
    activate_allowed_o='0; candidate=0; candidate_rank=0; candidate_rfm=0; candidate_extra=0;
    periodic_due=0; rfm_due=0; all_bank_due=0;candidate_age=0; rank_age=0; budget_value=64'hffffffff;
    for(int r=0;r<2;r++) begin
      if(r<int'(profile_i.ranks)) begin
        for(int p=0;p<8;p++) begin
          activate_allowed_o[r][p]=running_q && !fault_o && !self_refresh_q[r]
            && (!profile_i.mr27[0] || raa_q[r][p]<maximum);
        end
        if(!self_refresh_q[r] && running_q) begin
          if(ck_q-last_refresh_q[r]>=64'(interval_value)) budget_value=0;
          else if(64'(interval_value)-(ck_q-last_refresh_q[r])<budget_value) budget_value=64'(interval_value)-(ck_q-last_refresh_q[r]);
          periodic_due=thermal_debt_q[r] || ck_q-last_refresh_q[r]+64'(reserve_value)>=64'(interval_value)
            || next_pair_q[r]!=0 || all_bank_round_q[r];
          if(periodic_due && !effective_per_bank_o) all_bank_due=1;
          rfm_due=0;
          for(int p=0;p<8;p++) if(profile_i.mr27[0] && raa_q[r][p]+1>=maximum) rfm_due=1;
          // Interleave ranks by their last issued pair, rather than holding
          // one rank's unchanged full-round timestamp until all eight pairs
          // complete. Each rank retains its own prescribed pair order.
          rank_age=ck_q-(effective_per_bank_o && periodic_due ? last_pair_q[r] : last_refresh_q[r]);
          if((!candidate || rank_age>candidate_age) && (periodic_due || rfm_due || (self_refresh_request_i && extra_q[r]))) begin
            candidate_age=rank_age;
            candidate=1; candidate_rank=1'(r); candidate_rfm=!periodic_due && rfm_due;
            candidate_extra=!periodic_due && !rfm_due && self_refresh_request_i && extra_q[r];
          end
        end
      end
    end
    refresh_budget_o=32'(budget_value);
    urgent_o=running_q && (pending_q || candidate || fault_o);
    request_valid_o=pending_q && running_q && !fault_o;
    request_rank_o=pending_rank_q; request_rfm_o=pending_rfm_q; request_all_o=pending_all_q; request_pair_o=pending_pair_q;
    // Poll before a complete REFpb round can consume the last MR4 window.
    // The lead covers traffic/precharge/refresh drain, serialized rank MRR
    // service and arbitration slack. Preserve the earlier 600-CK lead when
    // it is already more conservative (including the all-bank policy).
    poll_lead=maintenance_admission_budget(effective_profile_o,effective_per_bank_o)
      +32'(profile_i.ranks)*register_service_budget(effective_profile_o)+64;
    if(poll_lead<600) poll_lead=600;
    mr4_request_o=running_q && !fault_o && ck_q-last_mr4_q+64'(poll_lead)>=64'd3000;
    zq_request_o=running_q && !fault_o && ck_q-last_zq_q+64'(poll_lead)>=64'd3000;
  end
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if(!rst_ni) begin
      ck_q<=0; running_q<=0; self_refresh_q<=0; extra_q<=0; thermal_debt_q<=0;all_bank_round_q<=0;
      pending_q<=0; pending_rank_q<=0; pending_rfm_q<=0; pending_all_q<=1; pending_pair_q<=0; pending_extra_q<=0;
      thermal_o<=9; thermal_deadline_q<=0; fault_o<=0; diagnostic_o<=0; last_mr4_q<=0; last_zq_q<=0;
      for(int r=0;r<2;r++) begin last_refresh_q[r]<=0; last_pair_q[r]<=0; next_pair_q[r]<=0; for(int p=0;p<8;p++) raa_q[r][p]<=0; end
    end else if(destructive_reset_i) begin
      running_q<=0; self_refresh_q<=0; extra_q<=0; thermal_debt_q<=0;all_bank_round_q<=0; pending_q<=0;
      thermal_o<=9; thermal_deadline_q<=0; fault_o<=0; last_mr4_q<=ck_q; last_zq_q<=ck_q;
      for(int r=0;r<2;r++) begin last_refresh_q[r]<=ck_q; last_pair_q[r]<=ck_q; next_pair_q[r]<=0; for(int p=0;p<8;p++) raa_q[r][p]<=0; end
    end else begin
      ck_q<=ck_q+1;
      if(start_i && !running_q && !fault_o) begin
        running_q<=1; last_mr4_q<=ck_q; last_zq_q<=ck_q;
        for(int r=0;r<2;r++) begin last_refresh_q[r]<=ck_q; last_pair_q[r]<=ck_q; end
      end
      if(running_q && !fault_o) begin
        // Keep all-bank periodic service in one rank group. Independently
        // staggered ranks can otherwise overlap REF recovery forever at a
        // hot interval, denying the global pin/page idle boundary for MR4.
        // Pull in each non-SR rank once, and discharge only on actual REF.
        if(all_bank_round_q==0 && all_bank_due)
          all_bank_round_q<=(2'b11>>(2-int'(profile_i.ranks))) & ~self_refresh_q;
        if(!pending_q && candidate) begin
          pending_q<=1; pending_rank_q<=candidate_rank; pending_rfm_q<=candidate_rfm;
          pending_all_q<=candidate_extra || candidate_rfm || !effective_per_bank_o || all_bank_round_q[candidate_rank]; pending_extra_q<=candidate_extra; pending_pair_q<=next_pair_q[candidate_rank];
        end
        if(event_i.row_opened) begin
          if(!activate_allowed_o[event_i.rank_index][event_i.bank[2:0]]) begin
            fault_o<=1;
            if(diagnostic_o==0) diagnostic_o<={4'd1,event_i.rank_index,event_i.bank,23'd0};
          end
          else if(profile_i.mr27[0]) raa_q[event_i.rank_index][event_i.bank[2:0]]<=raa_q[event_i.rank_index][event_i.bank[2:0]]+1;
        end
        for(int r=0;r<2;r++) if(r<int'(profile_i.ranks)) begin
          if(self_refresh_enter_i[r]) begin
            if(extra_q[r] || urgent_o) begin
              fault_o<=1;
              if(diagnostic_o==0) diagnostic_o<={4'd2,1'(r),27'd0};
            end
            else self_refresh_q[r]<=1;
          end
          if(self_refresh_exit_i[r]) begin self_refresh_q[r]<=0; extra_q[r]<=1; last_refresh_q[r]<=ck_q; end
          if(external_refresh_i[r]) begin
            last_refresh_q[r]<=ck_q; last_pair_q[r]<=ck_q; thermal_debt_q[r]<=0; next_pair_q[r]<=0;
            all_bank_round_q[r]<=0;
            if(pending_q && pending_rank_q==1'(r)) pending_q<=0;
            for(int p=0;p<8;p++) raa_q[r][p]<=raa_q[r][p]>imt ? raa_q[r][p]-imt : 0;
          end
          if(!self_refresh_q[r] && !self_refresh_exit_i[r]
              && !external_refresh_i[r]
              && !(temperature_valid_i && new_interval!=0 && new_interval<interval_value && !thermal_debt_q[r])
              && ((thermal_debt_q[r] && ck_q>thermal_deadline_q) || (!thermal_debt_q[r] && ck_q-last_refresh_q[r]>64'(interval_value)))) begin
            fault_o<=1;
            if(diagnostic_o==0) diagnostic_o<={4'd3,1'(r),thermal_o,22'(ck_q-last_refresh_q[r])};
          end
        end
        if(request_valid_o && request_ready_i) begin
          pending_q<=0;
          for(int p=0;p<8;p++) if(pending_all_q || p==int'(pending_pair_q))
            raa_q[pending_rank_q][p]<=raa_q[pending_rank_q][p]>(pending_rfm_q ? decrement : imt) ? raa_q[pending_rank_q][p]-(pending_rfm_q ? decrement : imt) : 0;
          if(!pending_rfm_q) begin
            last_pair_q[pending_rank_q]<=ck_q;
            if(pending_all_q || pending_pair_q==7) begin
              if(pending_extra_q) extra_q[pending_rank_q]<=0;
              else begin
                last_refresh_q[pending_rank_q]<=ck_q; thermal_debt_q[pending_rank_q]<=0;
                if(pending_all_q) all_bank_round_q[pending_rank_q]<=0;
              end
            end
            if(!pending_extra_q) next_pair_q[pending_rank_q]<=pending_all_q ? 3'd0 : pending_pair_q+1;
          end
        end
        if(temperature_valid_i) begin
          if(new_interval==0) begin
            fault_o<=1;
            if(diagnostic_o==0) diagnostic_o<={4'd4,temperature_i,23'd0};
          end
          else begin
            thermal_o<=temperature_i;
            // A newly observed shorter interval can expose debt. Reserve one
            // bounded emergency drain; subsequent reports cannot extend it.
            for(int r=0;r<2;r++) if(r<int'(profile_i.ranks) && !self_refresh_q[r]
                && ck_q-last_refresh_q[r]>=64'(new_interval) && !thermal_debt_q[r]) begin
              thermal_debt_q[r]<=1;
              if(thermal_debt_q==0) thermal_deadline_q<=ck_q+64'(reserve_value);
            end
          end
        end
        if(mr4_done_i) last_mr4_q<=ck_q;
        if(zq_done_i) last_zq_q<=ck_q;
        if((self_refresh_q & (2'b11>>(2-int'(profile_i.ranks))))==(2'b11>>(2-int'(profile_i.ranks)))) begin
          last_mr4_q<=ck_q; last_zq_q<=ck_q;
        end else if(ck_q-last_mr4_q>3000 && !mr4_done_i) begin
          fault_o<=1;
          if(diagnostic_o==0) diagnostic_o<={4'd5,28'(ck_q-last_mr4_q)};
        end
      end
    end
  end
endmodule
