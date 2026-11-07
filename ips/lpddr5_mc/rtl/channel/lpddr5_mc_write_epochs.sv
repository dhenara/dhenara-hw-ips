// Rank-local MR43 membership survives recyclable physical-transfer slots.
// A posted notice transfers response storage; the final result settles integrity.
module lpddr5_mc_write_epochs (
  input logic clk_ck_i, rst_ni, destructive_reset_i, ownership_loss_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_profile_t profile_i,
  input logic posted_i,
  input logic add_valid_i,
  output logic add_ready_o,
  input logic add_rank_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_identity_t add_identity_i,
  input logic physical_valid_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_completion_t physical_i,
  input logic flush_i,
  input logic [31:0] drain_reserve_i,
  output logic drain_requested_o,
  output logic poll_valid_o,
  input logic poll_ready_i,
  output logic poll_rank_o,
  input logic result_valid_i, result_rank_i, result_failed_i,
  input logic feedback_valid_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_response_feedback_t feedback_i,
  output logic completion_valid_o,
  input logic completion_ready_i,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_completion_t completion_o,
  output logic drained_o, fault_o, late_error_o,
  output logic [63:0] stale_feedback_o
);
  import lpddr5_mc_channel_pkg::*;
  typedef struct packed {
    logic valid, physical, failed, notice_sent, exposed, handshaken;
    logic [1:0] exposed_response;
    lpddr5_mc_channel_identity_t identity;
  } member_t;
  member_t [1:0][15:0] members_q;
  logic [1:0] closing_q, polling_q, settled_q, failed_q;
  logic [63:0] ck_q, first_q[2], reopen_q[2];
  logic [4:0] count_q[2];
  logic output_rank_q, output_final_q;
  logic [3:0] output_index_q;
  int free_index, selected_index;
  logic duplicate, all_physical, feedback_match, physical_match, choose_rank, choose_final;
  logic [63:0] bound_value;
  logic unused_profile;
  assign unused_profile=^{profile_i,physical_i};
  always_comb begin
    free_index=-1; duplicate=0; drained_o=1; physical_match=0; feedback_match=0;
    for (int r=0;r<2;r++) for (int j=0;j<16;j++) begin
      if (members_q[r][j].valid) begin
        drained_o=0;
        if (members_q[r][j].identity==add_identity_i) duplicate=1;
        if (members_q[r][j].identity==physical_i.identity) physical_match=1;
        if (members_q[r][j].identity==feedback_i.identity) feedback_match=1;
      end else if (r==int'(add_rank_i) && free_index<0) free_index=j;
    end
    for (int r=0;r<2;r++)
      if (closing_q[r] || polling_q[r] || settled_q[r] || ck_q<reopen_q[r]) drained_o=0;
    drain_requested_o=|(closing_q & ~settled_q);
    bound_value=64'(profile_i.management_ck_mr43_poll_bound);
    add_ready_o=!fault_o && !ownership_loss_i && !duplicate && free_index>=0
      && int'(add_rank_i)<int'(profile_i.ranks) && !closing_q[add_rank_i]
      && !polling_q[add_rank_i] && !settled_q[add_rank_i] && ck_q>=reopen_q[add_rank_i]
      && count_q[add_rank_i]<5'd16;
    poll_valid_o=0; poll_rank_o=0; all_physical=0;
    for (int r=0;r<2;r++) begin
      all_physical=1;
      for (int j=0;j<16;j++) if (members_q[r][j].valid && !members_q[r][j].physical) all_physical=0;
      // Both ranks share one register transport. A fixed rank priority can
      // spend the older epoch's remaining deadline servicing the newer one.
      if ((!poll_valid_o || first_q[r]<first_q[poll_rank_o]) && closing_q[r]
          && !polling_q[r] && !settled_q[r] && all_physical && !fault_o) begin
        poll_valid_o=1; poll_rank_o=1'(r);
      end
    end
    selected_index=-1; choose_rank=0; choose_final=0;
    for (int r=0;r<2;r++) for (int j=0;j<16;j++) begin
      if (selected_index<0 && members_q[r][j].valid) begin
        if (posted_i && members_q[r][j].physical && !members_q[r][j].notice_sent) begin
          selected_index=j; choose_rank=1'(r); choose_final=0;
        end else if ((settled_q[r] || fault_o) && (!posted_i || members_q[r][j].notice_sent || !members_q[r][j].physical)) begin
          selected_index=j; choose_rank=1'(r); choose_final=1;
        end
      end
    end
  end
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if (!rst_ni) begin
      members_q<='0; closing_q<=0; polling_q<=0; settled_q<=0; failed_q<=0;
      ck_q<=0; fault_o<=0; late_error_o<=0; stale_feedback_o<=0;
      completion_valid_o<=0; completion_o<='0; output_rank_q<=0; output_index_q<=0; output_final_q<=0;
      for (int r=0;r<2;r++) begin count_q[r]<=0; first_q[r]<=0; reopen_q[r]<=0; end
    end else if (destructive_reset_i) begin
      members_q<='0; closing_q<=0; polling_q<=0; settled_q<=0; failed_q<=0;
      fault_o<=0; completion_valid_o<=0; completion_o<='0;
      for (int r=0;r<2;r++) begin count_q[r]<=0; first_q[r]<=0; reopen_q[r]<=0; end
    end else begin
      ck_q<=ck_q+1;
      if (add_valid_i && add_ready_o) begin
        members_q[add_rank_i][free_index]<='0;
        members_q[add_rank_i][free_index].valid<=1;
        members_q[add_rank_i][free_index].identity<=add_identity_i;
        if (count_q[add_rank_i]==0) first_q[add_rank_i]<=ck_q;
        if (count_q[add_rank_i]==15) closing_q[add_rank_i]<=1;
      end
      for (int r=0;r<2;r++) begin
        // The pin owner supplies its actual drain/page/WCK/serialized-MRR
        // reserve. Membership alone cannot infer how soon pins are available.
        if (count_q[r]!=0 && (flush_i || ck_q-first_q[r]+64'(drain_reserve_i)>=bound_value)) closing_q[r]<=1;
        if (count_q[r]!=0 && !polling_q[r] && !settled_q[r] && ck_q-first_q[r]>=bound_value
            && !(poll_valid_o && poll_ready_i && poll_rank_o==1'(r))) fault_o<=1;
        case ({add_valid_i && add_ready_o && add_rank_i==1'(r),
            completion_valid_o && completion_ready_i && output_final_q && output_rank_q==1'(r)})
          2'b10: count_q[r]<=count_q[r]+1;
          2'b01: count_q[r]<=count_q[r]-1;
          default: begin end
        endcase
        if (settled_q[r] && count_q[r]==0 && ck_q>=reopen_q[r]) begin
          closing_q[r]<=0; polling_q[r]<=0; settled_q[r]<=0; failed_q[r]<=0;
        end
      end
      if (poll_valid_o && poll_ready_i) polling_q[poll_rank_o]<=1;
      if (result_valid_i) begin
        if (!polling_q[result_rank_i] || settled_q[result_rank_i]) fault_o<=1;
        else begin
          settled_q[result_rank_i]<=1; failed_q[result_rank_i]<=result_failed_i;
          reopen_q[result_rank_i]<=ck_q+64'(profile_i.dram_ck_tmrr)+64'(profile_i.dram_ck_tmrw);
          if (result_failed_i) for (int j=0;j<16;j++)
            if (members_q[result_rank_i][j].valid && members_q[result_rank_i][j].exposed && members_q[result_rank_i][j].exposed_response==0) late_error_o<=1;
        end
      end
      for (int r=0;r<2;r++) for (int j=0;j<16;j++) begin
        if (members_q[r][j].valid) begin
          if (physical_valid_i && members_q[r][j].identity==physical_i.identity) begin
            if (!physical_i.physical_done || !physical_i.is_write || members_q[r][j].physical) fault_o<=1;
            members_q[r][j].physical<=physical_i.physical_done;
            members_q[r][j].failed<=physical_i.failed;
          end
          if (feedback_valid_i && members_q[r][j].identity==feedback_i.identity) begin
            if ((feedback_i.exposed || feedback_i.handshaken) && (!members_q[r][j].physical || !members_q[r][j].notice_sent) && posted_i) fault_o<=1;
            members_q[r][j].exposed<=members_q[r][j].exposed || feedback_i.exposed || feedback_i.handshaken;
            members_q[r][j].handshaken<=members_q[r][j].handshaken || feedback_i.handshaken;
            if (!members_q[r][j].exposed) members_q[r][j].exposed_response<=feedback_i.response;
            if ((fault_o || ownership_loss_i || failed_q[r] || (result_valid_i && result_rank_i==1'(r) && result_failed_i))
                && (feedback_i.exposed || feedback_i.handshaken) && feedback_i.response==0) late_error_o<=1;
          end
          if (ownership_loss_i && members_q[r][j].exposed && members_q[r][j].exposed_response==0) late_error_o<=1;
        end
      end
      if (physical_valid_i && !physical_match) fault_o<=1;
      if (feedback_valid_i && !feedback_match && stale_feedback_o!='1) stale_feedback_o<=stale_feedback_o+1;
      if (ownership_loss_i) fault_o<=1;
      if (completion_valid_o && completion_ready_i) begin
        completion_valid_o<=0;
        if (output_final_q) begin
          members_q[output_rank_q][output_index_q].valid<=0;
        end else members_q[output_rank_q][output_index_q].notice_sent<=1;
      end
      // Registered output is immutable while backpressured, including a fault
      // arriving on that edge. Host failure settlement owns its bus response.
      if (!completion_valid_o && selected_index>=0) begin
        completion_valid_o<=1; output_rank_q<=choose_rank; output_index_q<=4'(selected_index); output_final_q<=choose_final;
        completion_o<='0; completion_o.identity<=members_q[choose_rank][selected_index].identity;
        completion_o.is_write<=1; completion_o.physical_done<=members_q[choose_rank][selected_index].physical;
        completion_o.integrity_done<=choose_final;
        completion_o.failed<=members_q[choose_rank][selected_index].failed || (failed_q[choose_rank] || fault_o);
        completion_o.uncorrectable<=members_q[choose_rank][selected_index].failed || failed_q[choose_rank] || fault_o || ownership_loss_i;
        completion_o.response<=(members_q[choose_rank][selected_index].failed || (failed_q[choose_rank] || fault_o)) ? 2'b10 : 2'b00;
      end
    end
  end
endmodule
