// Registered sampled-edge DFI updates. Drain and retention admission are
// supplied by the containing CK owner; a timeout never returns ownership.
module lpddr5_mc_update (
  input logic clk_ck_i, rst_ni, destructive_reset_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_profile_t profile_i,
  input logic controller_request_i, drained_i, wck_disabled_i,
  input logic [31:0] refresh_budget_i,
  input logic controller_ack_i, phy_request_i,
  input logic [1:0] phy_type_i,
  output logic controller_request_o, phy_ack_o,
  output logic busy_o, done_o, fault_o
);
  import lpddr5_mc_channel_pkg::*;
  typedef enum logic [1:0] {IDLE, CTRL, PHY} state_t;
  state_t state_q;
  logic [31:0] elapsed_q, phy_age_q;
  logic [1:0] phy_type_q;
  logic phy_pending_q, ack_seen_q, ack_completed_q;
  logic [31:0] sample_edge, phy_limit;
  logic unused_profile;
  assign unused_profile=^profile_i;
  assign sample_edge=elapsed_q+1;
  assign busy_o=state_q!=IDLE || phy_pending_q || phy_request_i || fault_o;
  always_comb begin
    case (phy_type_q)
      0: phy_limit=32'(profile_i.management_ck_tphyupd_type0);
      1: phy_limit=32'(profile_i.management_ck_tphyupd_type1);
      2: phy_limit=32'(profile_i.management_ck_tphyupd_type2);
      default: phy_limit=32'(profile_i.management_ck_tphyupd_type3);
    endcase
  end
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q<=IDLE; elapsed_q<=0; phy_age_q<=0; phy_type_q<=0;
      phy_pending_q<=0; ack_seen_q<=0; ack_completed_q<=0;
      controller_request_o<=0; phy_ack_o<=0; done_o<=0; fault_o<=0;
    end else if (destructive_reset_i) begin
      state_q<=IDLE; elapsed_q<=0; phy_age_q<=0; phy_type_q<=0;
      phy_pending_q<=0; ack_seen_q<=0; ack_completed_q<=0;
      controller_request_o<=0; phy_ack_o<=0; done_o<=0; fault_o<=0;
    end else begin
      done_o<=0;
      if (!fault_o) begin
        if (phy_request_i && !phy_pending_q && state_q!=PHY) begin
          phy_pending_q<=1; phy_type_q<=phy_type_i; phy_age_q<=0;
        end else if (phy_pending_q) begin
          phy_age_q<=phy_age_q+1;
          if (!phy_request_i || phy_type_i!=phy_type_q || phy_age_q+1>32'(profile_i.management_ck_tphyupd_resp)) fault_o<=1;
        end
        case (state_q)
          IDLE: begin
            elapsed_q<=0;
            if (controller_ack_i) fault_o<=1;
            else if (phy_pending_q && phy_request_i && drained_i && wck_disabled_i
                && refresh_budget_i>=update_admission_budget(profile_i,1'b1,phy_type_q)) begin
              phy_ack_o<=1; state_q<=PHY; phy_pending_q<=0;
            end else if (controller_request_i && !phy_request_i && !phy_pending_q && drained_i && wck_disabled_i
                && refresh_budget_i>=update_admission_budget(profile_i,1'b0,2'b0)) begin
              if (profile_i.management_ck_tctrlupd_min<2 || 32'(profile_i.management_ck_tctrlupd_max)<32'(profile_i.management_ck_tctrlupd_min)+2) fault_o<=1;
              else begin
                controller_request_o<=1; state_q<=CTRL; ack_seen_q<=0; ack_completed_q<=0;
              end
            end
          end
          CTRL: begin
            elapsed_q<=sample_edge;
            if (controller_ack_i) begin
              if (ack_completed_q || (!ack_seen_q && sample_edge>=32'(profile_i.management_ck_tctrlupd_min))
                  || sample_edge>=32'(profile_i.management_ck_tctrlupd_max)-1) fault_o<=1;
              else ack_seen_q<=1;
            end else if (ack_seen_q && !ack_completed_q) begin
              if (sample_edge>32'(profile_i.management_ck_tctrlupd_max)-2) fault_o<=1;
              else ack_completed_q<=1;
            end else if ((!ack_seen_q || ack_completed_q) && sample_edge>=32'(profile_i.management_ck_tctrlupd_min)) begin
              controller_request_o<=0; state_q<=IDLE; done_o<=1;
            end
          end
          PHY: begin
            elapsed_q<=sample_edge;
            if (controller_ack_i || (phy_request_i && phy_type_i!=phy_type_q) || sample_edge>phy_limit) fault_o<=1;
            else if (!phy_request_i) begin phy_ack_o<=0; state_q<=IDLE; done_o<=1; end
          end
          default: fault_o<=1;
        endcase
      end
    end
  end
endmodule
