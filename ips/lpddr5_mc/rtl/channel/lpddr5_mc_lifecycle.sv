// CK-domain lifecycle owner. Sideband rank reports and PHY refresh execution
// are explicit integration attestations; a timer cannot manufacture either.
module lpddr5_mc_lifecycle (
  input logic clk_ck_i, rst_ni,
  input logic cold_rst_ni,
  input lpddr5_mc_channel_pkg::lpddr5_mc_profile_t profile_i,
  input logic profile_valid_i,
  input logic [31:0] profile_epoch_i,
  input logic command_valid_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_command_t command_i,
  output logic command_ready_o, command_rejected_o,
  input logic host_settled_i, backend_drained_i, integrity_drained_i,
  input logic pages_closed_i, wck_disabled_i, maintenance_urgent_i, extra_refresh_i,
  input logic [31:0] refresh_budget_i,
  input logic fault_i, reset_confirmed_i,
  input logic train_ack_i,
  input logic [31:0] train_token_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_management_in_t management_i,
  // Reports must go low after a new master acknowledgement and be reasserted
  // at return with the same rank states. They must be derived at the PHY seam.
  input logic [1:0] rank_report_valid_i, rank_self_refresh_i, phy_refresh_done_i,
  input logic update_busy_i, update_done_i, update_fault_i,
  output logic update_request_o,update_window_o,
  input logic fsp_busy_i, fsp_done_i, fsp_declined_i, fsp_fault_i,
  output logic fsp_request_o,
  output logic admission_o, issue_o, close_o, stop_o,
  output logic maintenance_enable_o,
  output logic destructive_reset_o, maintenance_start_o,
  output logic [1:0] self_refresh_enter_o, self_refresh_exit_o, external_refresh_o,
  output logic extra_service_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_ca_t ca_o,
  output logic lp_ctrl_request_o, lp_data_request_o, master_ack_o,
  output logic [1:0] syscs_state_o, phy_refresh_request_o,
  output logic [2:0] state_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_ownership_t owner_o,
  output logic fault_o, trusted_o, train_request_o, train_done_o,
  output logic [31:0] boot_epoch_o,
  output logic [31:0] first_fault_o,
  output logic [7:0] fault_code_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  typedef enum logic [4:0] {BOOT, BOOT_START, BOOT_EXIT, BOOT_WAIT,
    QUIET, RUN, DRAIN, LP_ACCEPT, LP_ENTER, LP_HOLD, LP_RELEASE,
    LP_UPDATE, LP_EXIT, LP_WAIT, UPDATE, FSP, MASTER_ENTER, MASTER_GUARD, MASTER,
    MASTER_RETURN, MASTER_EXIT, MASTER_WAIT, FAILED, PD_LOW} state_t;
  state_t state_q;
  lpddr5_mc_channel_command_t operation_q;
  logic [31:0] elapsed_q, retired_epoch_q, master_age_q, master_elapsed_q, boot_profile_epoch_q;
  logic retired_valid_q, init_low_q, boot_attested_q, lp_seen_q, op_started_q;
  logic master_pending_q, master_fresh_q;
  logic [1:0] master_type_q, master_choices_q, chosen_sr_q, report_low_q;
  logic master_sel_q;
  logic [1:0][31:0] refresh_age_q;
  logic [1:0] active_ranks, boot_sr_q;
  logic drained, known_ranks;
  logic [31:0] master_limit, wake_limit, residency_limit;
  // All admitted profiles keep CK at 800 MHz. JESD209-5C Figure 150 /
  // Table 245 require tCSPD >= 10 ns + 1 CK, tCSH >= 3 ns and tCSL >=
  // 4 ns; self-refresh requires at least 15 ns before exit. Round up in CK.
  localparam int PD_RESIDENCY_CK=9, SR_RESIDENCY_CK=12;
  localparam int PD_CS_HIGH_CK=3, PD_CS_LOW_CK=4;
  logic unused_payload;
  assign unused_payload=^{profile_i,management_i,update_busy_i,fsp_busy_i,master_choices_q,master_sel_q};
  assign active_ranks=2'b11>>(2-int'(profile_i.ranks));
  assign drained=host_settled_i && backend_drained_i && integrity_drained_i;
  assign known_ranks=(rank_report_valid_i & active_ranks)==active_ranks;
  // An accepted WAKE must survive the minimum residency bound. Do not
  // acknowledge the command in LP_HOLD until it can actually take effect.
  assign command_ready_o=state_q inside {QUIET,RUN,FAILED}
    || (state_q==LP_HOLD && elapsed_q+1>=residency_limit);
  // The upstream host closes new source admission before requesting drain.
  // Already accepted AXI work may still be in its CDC queues, so channel
  // ingress must remain live until the actual host settlement witness. This
  // applies to pending PHY-master entry too; blocking on master_pending_q in
  // DRAIN would strand that work and prevent ownership transfer indefinitely.
  assign admission_o=((state_q==RUN && !master_pending_q) || (state_q==DRAIN && !host_settled_i))
    && trusted_o && profile_valid_i && !maintenance_urgent_i && !fault_i;
  assign issue_o=(state_q inside {RUN,DRAIN}) && trusted_o && !fault_i;
  assign maintenance_enable_o=state_q inside {QUIET,RUN,DRAIN};
  assign close_o=state_q==DRAIN && backend_drained_i && integrity_drained_i;
  assign stop_o=(state_q inside {QUIET,DRAIN}) && backend_drained_i;
  assign extra_service_o=state_q==DRAIN && (operation_q==CH_CMD_SELF_REFRESH || (master_pending_q && chosen_sr_q!=0)) && extra_refresh_i;
  assign update_window_o=state_q inside {UPDATE,LP_UPDATE};
  assign update_request_o=(state_q inside {UPDATE,LP_UPDATE}) && !op_started_q;
  assign fsp_request_o=state_q==FSP && !op_started_q;
  assign fault_o=state_q==FAILED;
  always_comb begin
    residency_limit=32'(profile_i.dram_ck_tcke);
    if(operation_q==CH_CMD_POWER_DOWN && residency_limit<PD_RESIDENCY_CK) residency_limit=PD_RESIDENCY_CK;
    if(operation_q==CH_CMD_SELF_REFRESH && residency_limit<SR_RESIDENCY_CK) residency_limit=SR_RESIDENCY_CK;
    case(master_type_q)
      0: master_limit=32'(profile_i.management_ck_tphymstr_type0);
      1: master_limit=32'(profile_i.management_ck_tphymstr_type1);
      2: master_limit=32'(profile_i.management_ck_tphymstr_type2);
      default: master_limit=32'(profile_i.management_ck_tphymstr_type3);
    endcase
    wake_limit=profile_i.management_ck_tlp_ctrl_wakeup>profile_i.management_ck_tlp_data_wakeup
      ? 32'(profile_i.management_ck_tlp_ctrl_wakeup) : 32'(profile_i.management_ck_tlp_data_wakeup);
    ca_o='0;
    if(state_q inside {BOOT_EXIT,LP_EXIT,MASTER_EXIT}) begin
      ca_o.cs=state_q==BOOT_EXIT ? boot_sr_q : state_q==MASTER_EXIT ? chosen_sr_q : active_ranks;
      ca_o.address=ca_pack(7'd40,7'd0);
      if(state_q==LP_EXIT && operation_q==CH_CMD_POWER_DOWN) ca_o.address=0;
    end
    if(state_q==LP_ENTER) begin ca_o.cs=active_ranks; ca_o.address=ca_pack(operation_q==CH_CMD_SELF_REFRESH ? 7'd104 : 7'd64,7'd0); end
    if(state_q==MASTER_ENTER) begin ca_o.cs=chosen_sr_q; ca_o.address=ca_pack(7'd104,7'd0); end
    state_o=3'd3;
    if(state_q inside {BOOT,BOOT_START,BOOT_EXIT,BOOT_WAIT}) state_o=0;
    if(state_q==QUIET) state_o=1;
    if(state_q==RUN) state_o=2;
    if(state_q==LP_HOLD && operation_q==CH_CMD_SELF_REFRESH) state_o=4;
    if(state_q==FAILED) state_o=5;
    owner_o=LPDDR5_MC_OWN_QUIESCING;
    if(state_q inside {BOOT,BOOT_START}) owner_o=LPDDR5_MC_OWN_PHY;
    if(state_q inside {BOOT_EXIT,BOOT_WAIT,QUIET,RUN,LP_HOLD,LP_EXIT,PD_LOW,LP_WAIT}) owner_o=LPDDR5_MC_OWN_CONTROLLER;
    if(master_ack_o || state_q inside {UPDATE,FSP,LP_UPDATE}) owner_o=LPDDR5_MC_OWN_PHY;
    if(state_q==FAILED) owner_o=LPDDR5_MC_OWN_FAULT;
  end
  logic operational_q;
  logic [7:0] failure_code;
  // One ordered decoder supplies both volatile failure state and the retained
  // first-fault snapshot. Later checks keep the existing fault priority.
  always_comb begin
    failure_code=0;
    case(state_q)
      BOOT: if(profile_valid_i && elapsed_q>32'(profile_i.management_ck_boot_timeout)) failure_code=1;
      DRAIN: if(drained && pages_closed_i && wck_disabled_i && !maintenance_urgent_i
          && !master_pending_q && !management_i.phyupd_req && operation_q!=CH_CMD_UPDATE
          && operation_q inside {CH_CMD_POWER_DOWN,CH_CMD_SELF_REFRESH}
          && (!extra_refresh_i || operation_q==CH_CMD_POWER_DOWN)
          && (operation_q==CH_CMD_SELF_REFRESH || refresh_budget_i>wake_limit+32'(profile_i.dram_ck_txp)+32'(profile_i.management_ck_tlp_resp)+8)
          && (management_i.lp_ctrl_ack || management_i.lp_data_ack)) failure_code=2;
      LP_ACCEPT: if(!(management_i.lp_ctrl_ack && management_i.lp_data_ack && elapsed_q+1<=32'(profile_i.management_ck_tlp_resp))
          && elapsed_q+1>=32'(profile_i.management_ck_tlp_resp)
          && (lp_seen_q || management_i.lp_ctrl_ack || management_i.lp_data_ack)) failure_code=3;
      LP_HOLD: if(!management_i.lp_ctrl_ack || !management_i.lp_data_ack) failure_code=4;
      LP_RELEASE: if((management_i.lp_ctrl_ack || management_i.lp_data_ack) && elapsed_q+1>wake_limit) failure_code=5;
      MASTER: begin
        for(int r=0;r<2;r++) if(active_ranks[r] && !chosen_sr_q[r]) begin
          if(phy_refresh_request_o[r]) begin
            if(!phy_refresh_done_i[r] && refresh_age_q[r]+1>32'(profile_i.management_ck_tphymstr_rfsh)) failure_code=6;
          end else if(phy_refresh_done_i[r]) failure_code=7;
        end
        if(!management_i.phymstr_req && ((report_low_q & active_ranks)!=active_ranks || !known_ranks
            || (rank_self_refresh_i & active_ranks)!=chosen_sr_q || phy_refresh_request_o!=0)) failure_code=9;
        if(master_elapsed_q+1>master_limit) failure_code=8;
      end
      MASTER_RETURN: if(!master_fresh_q) failure_code=10;
      BOOT_START,BOOT_EXIT,BOOT_WAIT,QUIET,RUN,LP_ENTER,LP_UPDATE,LP_EXIT,LP_WAIT,
        UPDATE,FSP,MASTER_ENTER,MASTER_GUARD,MASTER_EXIT,MASTER_WAIT,FAILED,PD_LOW: begin end
      default: failure_code=11;
    endcase
    if(state_q!=FAILED && !destructive_reset_o) begin
      if(master_pending_q && (!management_i.phymstr_req || management_i.phymstr_type!=master_type_q
          || management_i.phymstr_cs_state!=master_choices_q || management_i.phymstr_state_sel!=master_sel_q
          || master_age_q+1>32'(profile_i.management_ck_tphymstr_resp))) failure_code=12;
      if(master_ack_o && management_i.phymstr_req && (management_i.phymstr_type!=master_type_q
          || management_i.phymstr_cs_state!=master_choices_q || management_i.phymstr_state_sel!=master_sel_q)) failure_code=13;
      if(management_i.error || fault_i || update_fault_i || fsp_fault_i) failure_code=14;
      if(state_q!=BOOT && state_q!=BOOT_START && !profile_valid_i) failure_code=15;
      if((management_i.lp_ctrl_ack || management_i.lp_data_ack)
          && !(state_q inside {LP_ACCEPT,LP_ENTER,LP_HOLD,LP_RELEASE})) failure_code=16;
    end
    if(!operational_q || state_q==FAILED) failure_code=0;
  end
  always_ff @(posedge clk_ck_i or negedge cold_rst_ni) begin
    if(!cold_rst_ni) first_fault_o<=0;
    else if(first_fault_o==0 && failure_code!=0)
      first_fault_o<={failure_code,boot_epoch_o[7:0],profile_epoch_i[7:0],3'b0,state_q};
  end
  // Identity must outlive every source ledger that can still reject its reuse.
  always_ff @(posedge clk_ck_i or negedge cold_rst_ni) begin
    if(!cold_rst_ni) boot_epoch_o<=0;
    else if(state_q==FAILED && command_valid_i && command_i==CH_CMD_RECOVER
      && host_settled_i && reset_confirmed_i && boot_epoch_o!='1) boot_epoch_o<=boot_epoch_o+1;
  end
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if(!rst_ni) begin
      operational_q<=0;state_q<=BOOT; operation_q<=CH_CMD_NONE; elapsed_q<=0;
      retired_epoch_q<=0; retired_valid_q<=0; boot_profile_epoch_q<=0; init_low_q<=0; boot_attested_q<=0;
      lp_seen_q<=0; op_started_q<=0; master_pending_q<=0; master_fresh_q<=0;
      master_age_q<=0; master_elapsed_q<=0; master_type_q<=0; master_choices_q<=0;
      master_sel_q<=0; chosen_sr_q<=0; boot_sr_q<=0; report_low_q<=0; refresh_age_q<=0;
      lp_ctrl_request_o<=0; lp_data_request_o<=0; master_ack_o<=0; syscs_state_o<=0; phy_refresh_request_o<=0;
      trusted_o<=0; train_request_o<=0; train_done_o<=0; fault_code_o<=0;
      command_rejected_o<=0; destructive_reset_o<=0; maintenance_start_o<=0;
      self_refresh_enter_o<=0; self_refresh_exit_o<=0; external_refresh_o<=0;
    end else begin
      operational_q<=1;
      command_rejected_o<=0; destructive_reset_o<=0; maintenance_start_o<=0;
      self_refresh_enter_o<=0; self_refresh_exit_o<=0; external_refresh_o<=0;
      elapsed_q<=elapsed_q+1;
      if(management_i.phymstr_req && !master_pending_q && !master_ack_o && state_q inside {QUIET,RUN,DRAIN}) begin
        master_pending_q<=1; master_age_q<=0; master_type_q<=management_i.phymstr_type;
        master_choices_q<=management_i.phymstr_cs_state; master_sel_q<=management_i.phymstr_state_sel;
        chosen_sr_q<=active_ranks & ~management_i.phymstr_cs_state & {2{management_i.phymstr_state_sel}};
      end else if(master_pending_q) master_age_q<=master_age_q+1;
      case(state_q)
        BOOT: begin
          if(!management_i.init_complete) init_low_q<=1;
          if(profile_valid_i) begin
            if(init_low_q && management_i.init_complete && known_ranks && wck_disabled_i
                && management_i.training_epoch==boot_epoch_o
                && (!retired_valid_q || profile_epoch_i>retired_epoch_q)) begin
              train_request_o<=1; boot_attested_q<=1; boot_profile_epoch_q<=profile_epoch_i; boot_sr_q<=rank_self_refresh_i & active_ranks;
            end
            if(train_ack_i) begin
              if(!train_request_o || !boot_attested_q || train_token_i!=boot_epoch_o || !known_ranks
                  || profile_epoch_i!=boot_profile_epoch_q || !management_i.init_complete || !wck_disabled_i
                  || (rank_self_refresh_i & active_ranks)!=boot_sr_q) command_rejected_o<=1;
              else begin train_done_o<=1; train_request_o<=0; maintenance_start_o<=1; state_q<=BOOT_START; elapsed_q<=0; end
            end
          end else begin elapsed_q<=0; if(train_ack_i) command_rejected_o<=1; end
        end
        BOOT_START: begin
          if(boot_sr_q!=0) begin state_q<=BOOT_EXIT; end
          else begin state_q<=QUIET; trusted_o<=1; end
        end
        BOOT_EXIT: begin self_refresh_exit_o<=boot_sr_q; elapsed_q<=0; state_q<=BOOT_WAIT; end
        BOOT_WAIT: if(elapsed_q+1>=32'(profile_i.dram_ck_txsr)) begin state_q<=QUIET; trusted_o<=1; end
        QUIET,RUN: begin
          if(master_pending_q || management_i.phyupd_req) begin
            state_q<=DRAIN; operation_q<=CH_CMD_NONE; elapsed_q<=0;
          end else if(command_valid_i) begin
            case(command_i)
              CH_CMD_ENABLE: if(profile_valid_i && trusted_o && !maintenance_urgent_i) state_q<=RUN; else command_rejected_o<=1;
              CH_CMD_QUIESCE,CH_CMD_UPDATE,CH_CMD_POWER_DOWN,CH_CMD_SELF_REFRESH,CH_CMD_FSP: begin
                operation_q<=command_i; state_q<=DRAIN; elapsed_q<=0;
              end
              default: command_rejected_o<=1;
            endcase
          end
        end
        DRAIN: if(drained && pages_closed_i && wck_disabled_i && !maintenance_urgent_i) begin
          elapsed_q<=0; op_started_q<=0;
          if(master_pending_q) begin
            if(chosen_sr_q==active_ranks || refresh_budget_i>=master_limit+32'(profile_i.management_ck_tphymstr_rfsh)+8)
              begin state_q<=MASTER_ENTER; syscs_state_o<=active_ranks; end
          end else if(management_i.phyupd_req || operation_q==CH_CMD_UPDATE) begin
            // Maintenance remains enabled in DRAIN until the operation can
            // actually start. Entering UPDATE early would prevent replenishing
            // the reserve that its engine is waiting for.
            if(refresh_budget_i>=update_admission_budget(profile_i,management_i.phyupd_req,management_i.phyupd_type)+2) state_q<=UPDATE;
          end else if(operation_q==CH_CMD_FSP) begin
            if(refresh_budget_i>=fsp_admission_budget(profile_i)+2) state_q<=FSP;
          end
          else if(operation_q inside {CH_CMD_POWER_DOWN,CH_CMD_SELF_REFRESH}) begin
            if(!extra_refresh_i || operation_q==CH_CMD_POWER_DOWN) begin
              if(operation_q==CH_CMD_SELF_REFRESH || refresh_budget_i>wake_limit+32'(profile_i.dram_ck_txp)+32'(profile_i.management_ck_tlp_resp)+8) begin
                if(!management_i.lp_ctrl_ack && !management_i.lp_data_ack) begin state_q<=LP_ACCEPT; lp_ctrl_request_o<=1; lp_data_request_o<=1; lp_seen_q<=0; end
              end
            end
          end else state_q<=QUIET;
        end
        UPDATE: begin
          if(update_busy_i) op_started_q<=1;
          if(update_done_i) begin state_q<=QUIET; op_started_q<=0; end
        end
        FSP: begin
          if(fsp_busy_i) op_started_q<=1;
          if(fsp_done_i || fsp_declined_i) begin state_q<=QUIET; op_started_q<=0; end
        end
        LP_ACCEPT: begin
          if(management_i.lp_ctrl_ack || management_i.lp_data_ack) lp_seen_q<=1;
          if(management_i.lp_ctrl_ack && management_i.lp_data_ack && elapsed_q+1<=32'(profile_i.management_ck_tlp_resp)) begin state_q<=LP_ENTER; elapsed_q<=0; end
          else if(elapsed_q+1>=32'(profile_i.management_ck_tlp_resp)) begin
            if(!lp_seen_q && !management_i.lp_ctrl_ack && !management_i.lp_data_ack) begin lp_ctrl_request_o<=0; lp_data_request_o<=0; state_q<=QUIET; end
          end
        end
        LP_ENTER: begin
          if(operation_q==CH_CMD_SELF_REFRESH) self_refresh_enter_o<=active_ranks;
          elapsed_q<=0; state_q<=LP_HOLD;
        end
        LP_HOLD: begin
          if(management_i.lp_ctrl_ack && management_i.lp_data_ack) begin
            if((command_valid_i && command_i==CH_CMD_WAKE) || (operation_q==CH_CMD_POWER_DOWN && refresh_budget_i<=wake_limit+32'(profile_i.dram_ck_txp)+8)) begin
              if(elapsed_q+1>=residency_limit) begin state_q<=LP_RELEASE; elapsed_q<=0; lp_ctrl_request_o<=0; lp_data_request_o<=0; end
              else command_rejected_o<=command_valid_i;
            end else if(command_valid_i) command_rejected_o<=1;
          end
        end
        LP_RELEASE: begin
          if(!management_i.lp_ctrl_ack && !management_i.lp_data_ack) begin
            elapsed_q<=0; op_started_q<=0; state_q<=operation_q==CH_CMD_SELF_REFRESH ? LP_UPDATE : LP_EXIT;
          end
        end
        LP_UPDATE: begin
          if(update_busy_i) op_started_q<=1;
          if(update_done_i) begin state_q<=LP_EXIT; op_started_q<=0; end
        end
        LP_EXIT: begin
          if(operation_q==CH_CMD_SELF_REFRESH) begin
            self_refresh_exit_o<=active_ranks;elapsed_q<=0;state_q<=LP_WAIT;
          end else if(elapsed_q+1>=PD_CS_HIGH_CK) begin elapsed_q<=0;state_q<=PD_LOW;end
        end
        PD_LOW: if(elapsed_q+1>=PD_CS_LOW_CK) begin elapsed_q<=0;state_q<=LP_WAIT;end
        LP_WAIT: if(elapsed_q+1>=(operation_q==CH_CMD_SELF_REFRESH ? 32'(profile_i.dram_ck_txsr) : 32'(profile_i.dram_ck_txp))) state_q<=QUIET;
        MASTER_ENTER: begin
          self_refresh_enter_o<=chosen_sr_q; elapsed_q<=0; state_q<=MASTER_GUARD;
        end
        MASTER_GUARD: if(chosen_sr_q==0 || elapsed_q+1>=32'(profile_i.dram_ck_tcke)) begin
          state_q<=MASTER; master_ack_o<=1; master_pending_q<=0; master_elapsed_q<=0; master_fresh_q<=0; report_low_q<=0; refresh_age_q<=0;
        end
        MASTER: begin
          master_elapsed_q<=master_elapsed_q+1;
          report_low_q<=report_low_q | (~rank_report_valid_i & active_ranks);
          for(int r=0;r<2;r++) if(active_ranks[r] && !chosen_sr_q[r]) begin
            if(!phy_refresh_request_o[r] && refresh_budget_i<=32'(profile_i.management_ck_tphymstr_rfsh)+8) begin phy_refresh_request_o[r]<=1; refresh_age_q[r]<=0; end
            if(phy_refresh_request_o[r]) begin
              refresh_age_q[r]<=refresh_age_q[r]+1;
              if(phy_refresh_done_i[r]) begin external_refresh_o[r]<=1; phy_refresh_request_o[r]<=0; end
            end
          end
          if(!management_i.phymstr_req) begin
            if((report_low_q & active_ranks)==active_ranks && known_ranks && (rank_self_refresh_i & active_ranks)==chosen_sr_q && phy_refresh_request_o==0) begin master_ack_o<=0; master_fresh_q<=1; state_q<=MASTER_RETURN; elapsed_q<=0; end
          end
        end
        MASTER_RETURN: begin if(master_fresh_q) begin state_q<=chosen_sr_q==0 ? QUIET : MASTER_EXIT; end end
        MASTER_EXIT: begin self_refresh_exit_o<=chosen_sr_q; elapsed_q<=0; state_q<=MASTER_WAIT; end
        MASTER_WAIT: if(elapsed_q+1>=32'(profile_i.dram_ck_txsr)) state_q<=QUIET;
        FAILED: if(command_valid_i && command_i==CH_CMD_RECOVER) begin
          if(!host_settled_i || !reset_confirmed_i || boot_epoch_o=='1) command_rejected_o<=1;
          else begin
            destructive_reset_o<=1;
            if(profile_valid_i && (!retired_valid_q || profile_epoch_i>retired_epoch_q)) begin retired_epoch_q<=profile_epoch_i; retired_valid_q<=1; end
            state_q<=BOOT; trusted_o<=0; train_done_o<=0; train_request_o<=0; init_low_q<=0; boot_attested_q<=0; elapsed_q<=0;
            lp_ctrl_request_o<=0; lp_data_request_o<=0; master_ack_o<=0; syscs_state_o<=0; phy_refresh_request_o<=0;
            master_pending_q<=0; operation_q<=CH_CMD_NONE; fault_code_o<=0; op_started_q<=0;
          end
        end
        default: begin end
      endcase
      if(failure_code!=0) begin
        state_q<=FAILED;trusted_o<=0;fault_code_o<=failure_code;
      end
    end
  end
endmodule
