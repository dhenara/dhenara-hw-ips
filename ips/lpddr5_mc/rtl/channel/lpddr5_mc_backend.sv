// Physical channel owner. The register path borrows pins only after actual
// data drain, page recovery and WCK disable; queued logical requests may stay.
module lpddr5_mc_backend #(
  parameter int DEPTH=64,
  parameter int CHANNEL_INDEX=0
) (
  input logic clk_ck_i,rst_ni,destructive_reset_i,fault_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_config_t config_i,
  input logic admission_i,issue_i,service_i,close_i,stop_i,
  input logic [31:0] refresh_budget_i,
  input logic maintenance_per_bank_i,
  input logic request_valid_i,
  output logic request_ready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_granule_t request_i,
  output logic completion_valid_o,
  input logic completion_ready_i,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_completion_t completion_o,
  input logic maintenance_valid_i,maintenance_rank_i,maintenance_rfm_i,maintenance_all_i,
  input logic [2:0] maintenance_pair_i,
  output logic maintenance_ready_o,
  input logic [1:0][7:0] activate_allowed_i,
  input logic register_pending_i,register_valid_i,register_rank_i,register_zq_i,
  input logic [6:0] register_address_i,
  output logic register_ready_o,register_result_valid_o,register_result_rank_o,register_result_zq_o,register_result_failed_o,
  output logic [6:0] register_result_address_o,
  output logic [7:0] register_result_value_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_ca_t ca_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_wck4_t wck_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_write_bus4_t write_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_read_request4_t read_request_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_return4_t return_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_event_t event_o,
  output logic drained_o,pin_drained_o,integrity_drained_o,pages_closed_o,rows_closed_o,wck_disabled_o,fault_o,
  output logic [63:0] accepted_o,issued_o,replayed_o,max_wait_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic physical_valid,physical_ready,result_valid,result_ready,adapter_drained,adapter_fault;
  lpddr5_mc_granule_t physical,launch;
  lpddr5_mc_channel_completion_t result_value,data_result,epoch_result;
  lpddr5_mc_channel_identity_t launch_identity;
  logic launch_valid,launch_ready,data_ready,data_valid,data_result_ready,data_drained,data_fault;
  logic scheduler_drained,pair_pending,scheduler_fault,scheduler_stop;
  logic sync,sync_write,sync_rank,wck_reusable,wck_rank,wck_write,wck_fault,wck_stop_ready,emit_stop;
  logic epoch_ready,epoch_poll,epoch_poll_rank,epoch_poll_ready,epoch_valid,epoch_completion_ready,epoch_drained,epoch_fault,epoch_drain;
  logic [31:0] epoch_reserve;
  logic link_reserve_hold;
  logic epoch_return,epoch_return_rank,epoch_return_failed,epoch_late;
  logic register_owner_q,register_busy,register_fault,reg_ready,reg_valid,reg_rank,reg_zq,reg_failed;
  logic [6:0] reg_address;
  logic [7:0] reg_value;
  logic close_rank_q,block_issue,exclusive_ready,register_accept,is_epoch_q;
  lpddr5_mc_ca_t scheduler_ca,register_ca;
  lpddr5_mc_wck4_t main_wck,register_wck;
  lpddr5_mc_read_request4_t main_read,register_read;
  lpddr5_mc_return4_t main_return,register_return;
  logic [63:0] last_pin,stale_results,stale_feedback;
  logic unused_debug;
  assign unused_debug=^{last_pin,stale_results,stale_feedback,epoch_late,register_busy};
  assign fault_o=adapter_fault || data_fault || scheduler_fault || wck_fault || epoch_fault || register_fault;
  // REF recovery can consume the entire MR43 polling deadline. Finish a
  // link epoch before maintenance becomes urgent; do not start one inside
  // that reserved interval. Already accepted pairs still finish every CK.
  assign link_reserve_hold=config_i.profile.write_link
    && refresh_budget_i<=maintenance_admission_budget(config_i.profile,maintenance_per_bank_i)+epoch_reserve+8;
  // Keep the drain through rank/sweep handoffs and a refresh preemption.
  // Admission can temporarily lose its reserve while the register obligation
  // remains pending; that must not reopen rows before the next transaction.
  assign block_issue=link_reserve_hold || fault_i || fault_o || register_owner_q || epoch_drain || register_pending_i || maintenance_valid_i || close_i;
  // One accepted pair, the longest live data path and write recovery, PREab
  // for each rank, then every rank's serialized MRR/read-clear/WCK handback.
  assign epoch_reserve=32'(config_i.profile.management_ck_tctrl_delay)
    +(config_i.profile.wl_ck>config_i.profile.rl_ck ? 32'(config_i.profile.wl_ck) : 32'(config_i.profile.rl_ck))
    +32'(config_i.profile.dram_ck_twr)+32'(config_i.profile.dram_ck_trp_ab)
    +2*32'(config_i.profile.dram_ck_tppd)+16
    +32'(config_i.profile.ranks)*(32'(config_i.profile.rl_ck)+32'(config_i.profile.management_ck_tctrl_delay)
      +(32'(config_i.profile.dfi_wck_tphy_rdlat)+32'(config_i.profile.wck_twck_dis)+7)/(config_i.profile.ratio==2 ? 2 : 4)
      +32'(config_i.profile.dram_ck_tmrr)+32'(config_i.profile.dram_ck_tmrw)+16);
  assign pin_drained_o=data_drained && !pair_pending && !register_owner_q;
  assign integrity_drained_o=epoch_drained && adapter_drained;
  assign drained_o=adapter_drained && scheduler_drained && pin_drained_o && epoch_drained;
  assign exclusive_ready=service_i && !fault_i && !fault_o && data_drained && pages_closed_o && wck_disabled_o && !pair_pending && !register_owner_q && !maintenance_valid_i;
  assign register_accept=(epoch_poll || register_valid_i) && reg_ready;
  assign epoch_poll_ready=reg_ready && epoch_poll;
  assign register_ready_o=reg_ready && !epoch_poll;
  assign register_result_valid_o=reg_valid && !is_epoch_q;
  assign register_result_rank_o=reg_rank;
  assign register_result_zq_o=reg_zq;
  assign register_result_failed_o=reg_failed;
  assign register_result_address_o=reg_address;
  assign register_result_value_o=reg_value;
  assign epoch_return=reg_valid && is_epoch_q;
  assign epoch_return_rank=reg_rank;
  assign epoch_return_failed=reg_failed || reg_value!=0;

  lpddr5_mc_granule_adapter #(.DEPTH(DEPTH),.CHANNEL_INDEX(CHANNEL_INDEX)) adapter (
    .clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.destructive_reset_i(destructive_reset_i),.admission_i(admission_i && !fault_i && !fault_o),
    .boot_epoch_i(config_i.boot_epoch),.config_epoch_i(config_i.config_epoch),.rank_count_i(config_i.profile.ranks),
    .request_valid_i(request_valid_i),.request_ready_o(request_ready_o),.request_i(request_i),
    .physical_valid_o(physical_valid),.physical_ready_i(physical_ready),.physical_o(physical),
    .result_valid_i(result_valid),.result_i(result_value),.result_ready_o(result_ready),
    .completion_valid_o(completion_valid_o),.completion_ready_i(completion_ready_i),.completion_o(completion_o),
    .drained_o(adapter_drained),.fault_o(adapter_fault),.stale_results_o(stale_results));
  lpddr5_mc_scheduler #(.QUEUE_DEPTH(DEPTH),.CHANNEL_INDEX(CHANNEL_INDEX)) scheduler (
    .clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.abort_i(destructive_reset_i),.config_i(config_i),.admission_i(!fault_i && !fault_o),.issue_enable_i(issue_i && !block_issue),
    .command_enable_i(service_i && !register_owner_q && !fault_i && !fault_o),
    .request_valid_i(physical_valid),.request_ready_o(physical_ready),.request_i(physical),
    .launch_valid_o(launch_valid),.launch_ready_i(launch_ready),.launch_o(launch),.ca_o(scheduler_ca),.event_o(event_o),
    .sync_o(sync),.sync_write_o(sync_write),.sync_rank_o(sync_rank),.wck_disabled_i(wck_disabled_o),.wck_reusable_i(wck_reusable),.wck_rank_i(wck_rank),.wck_write_i(wck_write),
    .drained_o(scheduler_drained),.pair_pending_o(pair_pending),.fault_o(scheduler_fault),
    .maintenance_valid_i(maintenance_valid_i && service_i && data_drained && !register_owner_q && !fault_i && !fault_o),
    .maintenance_rank_i(maintenance_valid_i ? maintenance_rank_i : close_rank_q),.maintenance_rfm_i(maintenance_rfm_i),.maintenance_all_i(maintenance_all_i),.maintenance_pair_i(maintenance_pair_i),
    .activate_allowed_i(activate_allowed_i),.close_i(service_i && !fault_i && !fault_o && !register_owner_q && !maintenance_valid_i && (close_i || epoch_drain || register_pending_i)),
    .stop_requested_o(scheduler_stop),.maintenance_ready_o(maintenance_ready_o),.pages_closed_o(pages_closed_o),.rows_closed_o(rows_closed_o),
    .accepted_o(accepted_o),.issued_o(issued_o),.replayed_o(replayed_o),.max_wait_o(max_wait_o));
  always_comb begin
    launch_identity='0;launch_identity.tag=launch.tag;launch_identity.generation=launch.generation;
    launch_identity.boot_epoch=config_i.boot_epoch;launch_identity.config_epoch=config_i.config_epoch;
    launch_ready=data_ready && (!launch.is_write || !config_i.profile.write_link || epoch_ready);
    result_valid=epoch_valid || (data_valid && !(config_i.profile.write_link && data_result.is_write));
    result_value=epoch_valid ? epoch_result : data_result;
    epoch_completion_ready=result_ready;
    data_result_ready=(config_i.profile.write_link && data_result.is_write) || (result_ready && !epoch_valid);
    // WS_OFF owns an otherwise empty CA cycle. Accepted command pairs cannot
    // be displaced after the scheduler has updated its timing/credit state.
    emit_stop=(scheduler_stop || stop_i || epoch_drain || register_pending_i || maintenance_valid_i)
      && wck_stop_ready && data_drained && scheduler_ca.cs==0 && !register_owner_q && !pair_pending;
    ca_o=register_owner_q ? register_ca : scheduler_ca;
    if(emit_stop) begin ca_o.cs=2'b1<<wck_rank;ca_o.address=ca_cas(3'd7); end
    wck_o=register_owner_q ? register_wck : main_wck;
    read_request_o=register_owner_q ? register_read : main_read;
    main_return=register_owner_q ? lpddr5_mc_return4_t'('0) : return_i;
    register_return=register_owner_q ? return_i : lpddr5_mc_return4_t'('0);
  end
  lpddr5_mc_data #(.DEPTH(DEPTH)) data_path (
    .clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.abort_i(destructive_reset_i),.profile_i(config_i.profile),
    .launch_valid_i(launch_valid),.launch_ready_o(data_ready),.launch_i(launch),.identity_i(launch_identity),
    .done_valid_o(data_valid),.done_ready_i(data_result_ready),.done_o(data_result),.write_o(write_o),.read_request_o(main_read),.return_i(main_return),
    .drained_o(data_drained),.fault_o(data_fault),.last_pin_half_wck_o(last_pin));
  lpddr5_mc_wck wck (
    .clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.abort_i(destructive_reset_i),.profile_i(config_i.profile),
    .sync_i(sync),.sync_write_i(sync_write),.sync_rank_i(sync_rank),.access_i(launch_valid),.access_write_i(launch.is_write),.access_bl32_i(launch.is_64_bytes),
    .stop_i(emit_stop),.disabled_o(wck_disabled_o),.reusable_o(wck_reusable),.fault_o(wck_fault),.stop_ready_o(wck_stop_ready),.active_rank_o(wck_rank),.active_write_o(wck_write),.wck_o(main_wck));
  // Conservative response policy also covers inline physical writes: no
  // logical completion is offered until final MR43 integrity settlement.
  lpddr5_mc_write_epochs epochs (
    .clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.destructive_reset_i(destructive_reset_i),.ownership_loss_i(fault_i),.profile_i(config_i.profile),.posted_i(1'b0),
    .add_valid_i(launch_valid && launch.is_write && config_i.profile.write_link),.add_ready_o(epoch_ready),.add_rank_i(launch.rank_index),.add_identity_i(launch_identity),
    .physical_valid_i(data_valid && data_result_ready && data_result.is_write && config_i.profile.write_link),.physical_i(data_result),
    .flush_i(!admission_i || scheduler_drained),.drain_reserve_i(epoch_reserve),.drain_requested_o(epoch_drain),
    .poll_valid_o(epoch_poll),.poll_ready_i(epoch_poll_ready),.poll_rank_o(epoch_poll_rank),
    .result_valid_i(epoch_return),.result_rank_i(epoch_return_rank),.result_failed_i(epoch_return_failed),
    .feedback_valid_i(1'b0),.feedback_i(lpddr5_mc_channel_response_feedback_t'('0)),
    .completion_valid_o(epoch_valid),.completion_ready_i(epoch_completion_ready),.completion_o(epoch_result),
    .drained_o(epoch_drained),.fault_o(epoch_fault),.late_error_o(epoch_late),.stale_feedback_o(stale_feedback));
  lpddr5_mc_register_io registers (
    .clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.destructive_reset_i(destructive_reset_i),.profile_i(config_i.profile),
    .boot_epoch_i(config_i.boot_epoch),.config_epoch_i(config_i.config_epoch),
    .request_valid_i(epoch_poll || register_valid_i),.request_ready_o(reg_ready),.request_rank_i(epoch_poll ? epoch_poll_rank : register_rank_i),
    .request_zq_i(!epoch_poll && register_zq_i),.request_address_i(epoch_poll ? 7'd43 : register_address_i),
    .exclusive_i(exclusive_ready || register_owner_q),.busy_o(register_busy),.result_valid_o(reg_valid),.result_ready_i(1'b1),
    .result_rank_o(reg_rank),.result_zq_o(reg_zq),.result_failed_o(reg_failed),.result_address_o(reg_address),.result_value_o(reg_value),
    .ca_o(register_ca),.wck_o(register_wck),.read_request_o(register_read),.return_i(register_return),.fault_o(register_fault));
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if(!rst_ni) begin register_owner_q<=0;is_epoch_q<=0;close_rank_q<=0; end
    else if(destructive_reset_i) begin register_owner_q<=0;is_epoch_q<=0;close_rank_q<=0; end
    else begin
      if(config_i.profile.ranks==2) close_rank_q<=!close_rank_q; else close_rank_q<=0;
      if(register_accept) begin register_owner_q<=1;is_epoch_q<=epoch_poll; end
      if(reg_valid) register_owner_q<=0;
    end
  end
endmodule
