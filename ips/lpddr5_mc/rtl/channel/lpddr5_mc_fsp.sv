// Constant-CK FSP operation. An inactive complete profile is frozen before
// requesting PHY acceptance. MR16 FSP-WR[1:0]/FSP-OP[3:2] are dynamic controls.
module lpddr5_mc_fsp (
  input logic clk_ck_i, rst_ni, destructive_reset_i,
  input logic request_i, drained_i, wck_disabled_i,
  input logic [31:0] refresh_budget_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_profile_t current_profile_i, target_profile_i,
  input logic [1:0] active_fsp_i, target_fsp_i,
  input logic [4:0] current_frequency_i, target_frequency_i,
  input logic [31:0] active_epoch_i, target_epoch_i,
  input logic init_complete_i,
  output logic init_start_o,
  output logic [1:0] fsp_o, ratio_o,
  output logic [4:0] frequency_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_ca_t ca_o,
  output logic busy_o, declined_o, fault_o, clock_change_done_o,
  output logic target_ready_o,
  input logic publish_i,
  input logic [31:0] publish_epoch_i,
  output logic published_o,
  output lpddr5_mc_channel_pkg::lpddr5_mc_profile_t frozen_target_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  typedef enum logic [3:0] {IDLE, ACCEPT, MRW1, MRW2, MRWAIT, GUARD, STABLE, RELEASE, WAIT_RETURN, READY, HOLD} state_t;
  state_t state_q;
  lpddr5_mc_profile_t current_q;
  logic [31:0] target_epoch_q, elapsed_q, wait_q;
  logic [4:0] mr_index_q;
  logic [1:0] old_fsp_q;
  logic [4:0] old_frequency_q;
  logic accepted_q;
  logic [6:0] mr_address;
  logic [7:0] mr_value;
  logic [31:0] admission_budget, spacing;
  logic unused_profile;
  assign unused_profile=^{current_profile_i,current_q};
  function automatic logic [6:0] register_at(input logic [4:0] index_value);
    case(index_value)
      0,18: return 16;
      1: return 1; 2: return 2; 3: return 3; 4: return 10; 5: return 11;
      6: return 12; 7: return 14; 8: return 15; 9: return 17; 10: return 18;
      11: return 19; 12: return 20; 13: return 22; 14: return 24;
      15: return 30; 16: return 41; default: return 58;
    endcase
  endfunction
  always_comb begin
    admission_budget=fsp_admission_budget(current_profile_i);
    spacing=32'(current_q.dram_ck_tmrd)>32'(current_q.dram_ck_tmrw) ? 32'(current_q.dram_ck_tmrd) : 32'(current_q.dram_ck_tmrw);
    mr_address=register_at(mr_index_q);
    mr_value=frozen_target_o.mr[mr_address];
    if(mr_index_q==0) mr_value={frozen_target_o.mr[16][7:4],old_fsp_q,fsp_o};
    if(mr_index_q==18) mr_value={frozen_target_o.mr[16][7:4],fsp_o,fsp_o};
    ca_o='0;
    if((!fault_o || state_q==MRW2) && state_q inside {MRW1,MRW2}) begin
      ca_o.address=ca_mrw(mr_address,mr_value,state_q==MRW2);
      ca_o.cs=2'b11>>(2-int'(frozen_target_o.ranks));
    end
    busy_o=state_q!=IDLE || fault_o;
    target_ready_o=state_q==READY && !fault_o;
  end
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if(!rst_ni) begin
      state_q<=IDLE; current_q<='0; frozen_target_o<='0; target_epoch_q<=0;
      elapsed_q<=0; wait_q<=0; mr_index_q<=0; old_fsp_q<=0; old_frequency_q<=0; accepted_q<=0;
      init_start_o<=0; fsp_o<=0; ratio_o<=0; frequency_o<=0;
      declined_o<=0; fault_o<=0; clock_change_done_o<=0; published_o<=0;
    end else if(destructive_reset_i) begin
      state_q<=IDLE; frozen_target_o<='0; target_epoch_q<=0; elapsed_q<=0; wait_q<=0;
      init_start_o<=0; declined_o<=0; fault_o<=0; clock_change_done_o<=0; published_o<=0;
    end else begin
      declined_o<=0; published_o<=0;
      if(fault_o) begin if(state_q==MRW2) state_q<=HOLD; end
      else begin
        case(state_q)
          IDLE: if(request_i && drained_i && wck_disabled_i && refresh_budget_i>=admission_budget) begin
            if(!profile_valid(target_profile_i) || target_fsp_i>2 || target_fsp_i==active_fsp_i
                || target_frequency_i!=(target_profile_i.ratio==2 ? 5'd0 : 5'd1)
                || target_epoch_i<=active_epoch_i || !init_complete_i || target_profile_i.ranks!=current_profile_i.ranks) fault_o<=1;
            else begin
              state_q<=ACCEPT; current_q<=current_profile_i; frozen_target_o<=target_profile_i;
              old_fsp_q<=active_fsp_i; old_frequency_q<=current_frequency_i; fsp_o<=target_fsp_i; ratio_o<=lpddr5_mc_dfi_ratio(target_profile_i.ratio);
              frequency_o<=target_frequency_i; target_epoch_q<=target_epoch_i;
              init_start_o<=1; elapsed_q<=0; accepted_q<=0; clock_change_done_o<=0; mr_index_q<=0;
            end
          end
          ACCEPT: begin
            elapsed_q<=elapsed_q+1;
            if(!init_complete_i) begin
              if(elapsed_q+1>=32'(current_q.management_ck_tinit_start) && !accepted_q) fault_o<=1;
              else accepted_q<=1;
            end else if(accepted_q) fault_o<=1;
            if(elapsed_q+1>=32'(current_q.management_ck_tinit_start)) begin
              if(!accepted_q) begin init_start_o<=0; state_q<=IDLE; declined_o<=1; fsp_o<=old_fsp_q; frequency_o<=old_frequency_q; ratio_o<=lpddr5_mc_dfi_ratio(current_q.ratio); end
              else state_q<=MRW1;
            end
          end
          MRW1: begin state_q<=MRW2; if(init_complete_i || !wck_disabled_i) fault_o<=1; end
          MRW2: begin
            if(init_complete_i || !wck_disabled_i) fault_o<=1;
            wait_q<=spacing; state_q<=MRWAIT;
          end
          MRWAIT: begin
            if(init_complete_i || !wck_disabled_i) fault_o<=1;
            if(wait_q>1) wait_q<=wait_q-1;
            else if(mr_index_q<18) begin mr_index_q<=mr_index_q+1; state_q<=MRW1; end
            else begin wait_q<=32'(current_q.dram_ck_tfc)>201 ? 32'(current_q.dram_ck_tfc) : 201; state_q<=GUARD; end
          end
          GUARD: begin
            if(init_complete_i || !wck_disabled_i) fault_o<=1;
            if(wait_q>1) wait_q<=wait_q-1; else begin wait_q<=6; state_q<=STABLE; end
          end
          STABLE: begin
            if(init_complete_i || !wck_disabled_i) fault_o<=1;
            if(wait_q>1) wait_q<=wait_q-1; else begin clock_change_done_o<=1; state_q<=RELEASE; end
          end
          RELEASE: begin
            if(init_complete_i || !wck_disabled_i) fault_o<=1;
            init_start_o<=0; elapsed_q<=0; state_q<=WAIT_RETURN;
          end
          WAIT_RETURN: begin
            elapsed_q<=elapsed_q+1;
            if(!wck_disabled_i || elapsed_q+1>32'(current_q.management_ck_tinit_complete)) fault_o<=1;
            else if(init_complete_i) state_q<=READY;
          end
          READY: if(publish_i) begin
            if(publish_epoch_i!=target_epoch_q || !wck_disabled_i || !drained_i) fault_o<=1;
            else begin published_o<=1; state_q<=IDLE; end
          end
          default: fault_o<=1;
        endcase
      end
    end
  end
endmodule
