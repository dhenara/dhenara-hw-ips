// CK-domain composition. The host owns CDC and response exposure; this owner
// computes pin/integrity drain and admits configuration/ownership transitions.
module lpddr5_mc_channel #(
  parameter int DEPTH=64,
  parameter int CHANNEL_INDEX=0,
  parameter bit PER_BANK_REFRESH=0,
  parameter int NOTIFICATION_DEPTH=16
) (
  input logic clk_ck_i,rst_ni,
  input logic cold_rst_ni,
  input logic force_fault_i,
  input logic request_valid_i,
  output logic request_ready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_granule_t request_i,
  input logic [31:0] request_boot_epoch_i,request_config_epoch_i,
  output logic completion_valid_o,
  input logic completion_ready_i,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_completion_t completion_o,
  input logic feedback_valid_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_response_feedback_t feedback_i,
  input logic host_settled_i,reset_confirmed_i,
  input logic command_valid_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_command_t command_i,
  output logic command_ready_o,command_rejected_o,
  input logic train_ack_i,
  input logic [31:0] train_token_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_config_t config_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_config_mailbox_t mailbox_i,
  output logic prepared_valid_o,prepare_error_o,
  output logic [31:0] prepared_epoch_o,committed_epoch_o,
  output logic fsp_ready_o,fsp_declined_o,
  input logic [1:0] target_fsp_i,
  input logic [4:0] target_frequency_i,
  output logic notification_valid_o,
  input logic notification_ready_i,
  output logic [12:0] notification_events_o,
  output logic notification_error_valid_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_error_t notification_error_o,
  output logic [31:0] notification_boot_epoch_o,notification_config_epoch_o,
  input logic first_error_clear_i,
  input logic [63:0] first_error_clear_version_i,
  output logic first_error_valid_o,
  output logic [63:0] first_error_version_o,
  output logic [31:0] first_error_boot_epoch_o,first_error_config_epoch_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_management_in_t management_i,
  input logic [1:0] rank_report_valid_i,rank_self_refresh_i,phy_refresh_done_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_management_out_t management_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_ca_t ca_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_wck4_t wck_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_write_bus4_t write_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_read_request4_t read_request_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_return4_t return_i,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_status_t status_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  lpddr5_mc_channel_config_t active,prepared,effective_config;
  lpddr5_mc_profile_t effective_profile,frozen_target;
  logic active_valid,config_fault,config_drained,publication_legal;
  logic admission,issue_enable,close_pages,stop_wck,service,destructive_reset,maintenance_start;
  logic life_fault,trusted,train_request,train_done,extra_service,external_fault;
  logic [2:0] life_state;
  lpddr5_mc_ownership_t owner;
  logic [31:0] boot_epoch,first_fault;
  logic [7:0] fault_code;
  logic [1:0] sr_enter,sr_exit,external_refresh,syscs,phy_refresh_request;
  logic lp_ctrl_req,lp_data_req,master_ack;
  lpddr5_mc_ca_t life_ca,backend_ca,fsp_ca;
  logic backend_valid,backend_ready,backend_completion_valid,backend_completion_ready;
  lpddr5_mc_granule_t backend_request;
  lpddr5_mc_channel_completion_t backend_completion;
  logic backend_drained,pin_drained,integrity_drained,pages_closed,rows_closed,wck_disabled,backend_fault;
  logic [63:0] backend_accepted,issued,replayed,accepted_q,completed_q,stale_q;
  lpddr5_mc_event_t event_value;
  logic inline_ready,inline_physical_valid,inline_physical_ready,inline_completion_valid,inline_result_ready;
  logic inline_drained,inline_fault,inline_corrected,inline_uncorrectable;
  lpddr5_mc_granule_t inline_physical;
  lpddr5_mc_channel_completion_t inline_completion;
  logic maintenance_valid,maintenance_ready,maintenance_rank,maintenance_rfm,maintenance_all;
  logic [2:0] maintenance_pair;
  logic [1:0][7:0] activate_allowed;
  logic maintenance_urgent,maintenance_fault,maintenance_per_bank,extra_refresh,mr4_request,zq_request,mr4_done,zq_done;
  logic [31:0] refresh_budget,maintenance_diagnostic;
  logic [4:0] thermal,temperature;
  logic temperature_valid;
  logic temperature_sampled_q;
  logic update_request,update_window,update_busy,update_done,update_fault,ctrl_request,phy_ack;
  logic fsp_request,fsp_busy,fsp_published,fsp_declined,fsp_fault,fsp_init_start,fsp_clock_done,fsp_target_ready;
  logic [1:0] fsp_value,fsp_ratio,active_fsp_q;
  logic [4:0] fsp_frequency;
  logic register_pending,register_valid,register_ready,register_result_valid,register_result_rank,register_result_zq,register_result_failed;
  logic [6:0] register_result_address;
  logic [7:0] register_result_value;
  typedef enum logic [1:0] {REG_IDLE,REG_SEND,REG_WAIT} register_state_t;
  register_state_t register_state_q;
  logic register_rank_q,register_zq_q,register_fault_q;
  logic [4:0] hottest_q;
  logic [31:0] register_reserve;
  logic epochs_match,host_admission,quiescent;
  lpddr5_mc_channel_notification_t [2:0] occurrences;
  lpddr5_mc_channel_notification_t notification,first_error;
  logic [2:0] occurrence_valid;
  logic notification_overflow;
  logic train_request_q,train_done_q,config_valid_q,quiescent_q;
  logic config_fault_q,dfi_fault_q,maintenance_fault_q;
  logic [31:0] notification_epoch_q;
  logic [4:0] notification_temperature_q;
  logic dfi_fault;
  assign dfi_fault=life_fault || backend_fault || inline_fault || register_fault_q || update_fault || fsp_fault || management_i.error;
  // Full caller identity is retained until its completion moves into host RAM.
  lpddr5_mc_granule_t ledger_q[DEPTH];
  logic [DEPTH-1:0] ledger_valid_q;
  logic [63:0] ledger_accepted_ck_q[DEPTH];
  lpddr5_mc_channel_performance_t performance_q;
  logic [63:0] scheduler_max_wait;
  function automatic logic [63:0] saturating_add(input logic [63:0] value,increment);
    return value > 64'hffffffffffffffff-increment ? 64'hffffffffffffffff : value+increment;
  endfunction
  int free_ledger,done_ledger;
  logic unused_debug;
  assign unused_debug=^{backend_accepted,stale_q,feedback_valid_i,feedback_i,first_fault,maintenance_diagnostic,thermal,
    register_result_value,register_result_rank,register_result_zq,register_result_address,frozen_target,fsp_clock_done,first_error,inline_corrected,inline_uncorrectable};
  assign external_fault=force_fault_i || config_fault || backend_fault || inline_fault || maintenance_fault || register_fault_q || notification_overflow;
  assign fsp_ready_o=fsp_target_ready;
  assign fsp_declined_o=fsp_declined;
  assign epochs_match=request_boot_epoch_i==boot_epoch && request_config_epoch_i==committed_epoch_o;
  assign host_admission=admission && !external_fault && !life_fault && !maintenance_urgent && free_ledger>=0;
  assign quiescent=host_settled_i && backend_drained && inline_drained && pages_closed && wck_disabled;
  // A timing/profile change may publish only after the actual FSP operation.
  // Ordinary scheduler-only publication still requires true all-owner drain.
  assign publication_legal=!active_valid || config_i.profile==active.profile
    || (fsp_target_ready && config_i==prepared && config_crc(config_i)==mailbox_i.payload_crc && config_valid(config_i));
  assign config_drained=quiescent && !issue_enable && (!mailbox_i.commit || publication_legal);
  always_comb begin
    effective_config=active;effective_config.profile=effective_profile;
  end
  always_comb begin
    backend_request=active.inline_ecc ? inline_physical : request_i;
    backend_valid=active.inline_ecc ? inline_physical_valid : request_valid_i && host_admission && epochs_match;
    inline_physical_ready=backend_ready;
    request_ready_o=host_admission && (!epochs_match || (active.inline_ecc ? inline_ready : backend_ready));
    completion_valid_o=active.inline_ecc ? inline_completion_valid : backend_completion_valid;
    completion_o=active.inline_ecc ? inline_completion : backend_completion;
    backend_completion_ready=active.inline_ecc ? inline_result_ready : completion_ready_i;
    ca_o=backend_ca;
    if(life_ca.cs!=0) ca_o=life_ca;
    if(fsp_ca.cs!=0) ca_o=fsp_ca;
    management_o='0;
    management_o.ctrlupd_req=ctrl_request;management_o.phyupd_ack=phy_ack;
    management_o.init_start=fsp_init_start;management_o.phymstr_ack=master_ack;
    management_o.lp_ctrl_req=lp_ctrl_req;management_o.lp_data_req=lp_data_req;
    management_o.syscs_state=syscs;management_o.refresh_request=phy_refresh_request;
    management_o.frequency_ratio=fsp_busy ? fsp_ratio : lpddr5_mc_dfi_ratio(active.profile.ratio);
    management_o.frequency=fsp_busy ? fsp_frequency : (active.profile.ratio==2 ? 5'd0 : 5'd1);
    management_o.fsp=fsp_busy ? fsp_value : active_fsp_q;
    management_o.profile_epoch=committed_epoch_o;
    status_o='0;status_o.state=life_state;status_o.owner=owner;
    status_o.fault=life_fault || external_fault;status_o.memory_trusted=trusted && !external_fault;
    status_o.train_request=train_request;status_o.train_done=train_done;status_o.config_valid=active_valid;
    status_o.drained=quiescent;status_o.pin_drained=pin_drained && wck_disabled;
    status_o.ecc_drained=integrity_drained && inline_drained;
    status_o.boot_epoch=boot_epoch;status_o.config_epoch=committed_epoch_o;status_o.prepared_epoch=prepared_epoch_o;
    // Publish committed capability facts, never a staged/prepared descriptor.
    // The conservative reset temperature is an internal maintenance policy,
    // not an MR4 observation. Clear its readback until a fresh poll completes.
    status_o.device_mr8=active_valid ? active.profile.mr8 : 8'd0;
    status_o.device_mr27=active_valid ? active.profile.mr27 : 8'd0;
    status_o.device_mr57=active_valid ? active.profile.mr57 : 8'd0;
    status_o.temperature_mr4=temperature_sampled_q ? {3'b0,temperature} : 8'd0;
    status_o.power_policy=active.power_policy;status_o.power_down_idle_ck=active.power_down_idle_ck;
    status_o.self_refresh_idle_ck=active.self_refresh_idle_ck;
    status_o.events=0;status_o.fault_code=fault_code;status_o.first_error=first_error.error;
    status_o.accepted=accepted_q;status_o.issued=issued;status_o.completed=completed_q;status_o.replayed=replayed;
    status_o.performance=performance_q;status_o.performance.replays=replayed;status_o.performance.max_wait_ck=scheduler_max_wait;
  end
  always_comb begin
    free_ledger=-1;done_ledger=-1;
    for(int s=0;s<DEPTH;s++) begin
      if(!ledger_valid_q[s] && free_ledger<0) free_ledger=s;
      if(ledger_valid_q[s] && ledger_q[s].tag==completion_o.identity.tag && ledger_q[s].generation==completion_o.identity.generation) done_ledger=s;
    end
  end
  lpddr5_mc_channel_config #(.CHANNEL_INDEX(CHANNEL_INDEX)) configuration (
    .clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.destructive_reset_i(destructive_reset),.discard_prepared_i(fsp_declined),.boot_epoch_i(boot_epoch),
    .drained_i(config_drained),.config_i(config_i),.mailbox_i(mailbox_i),.prepared_valid_o(prepared_valid_o),.prepare_error_o(prepare_error_o),
    .prepared_epoch_o(prepared_epoch_o),.committed_epoch_o(committed_epoch_o),.active_valid_o(active_valid),.fault_o(config_fault),.prepared_o(prepared),.active_o(active));
  lpddr5_mc_inline #(.POISON_DEPTH(DEPTH),.CHANNEL_INDEX(CHANNEL_INDEX)) inline_ecc (
    .clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.destructive_reset_i(destructive_reset),.enable_i(active.inline_ecc),.admission_i(host_admission),
    .boot_epoch_i(boot_epoch),.config_epoch_i(committed_epoch_o),.rank_count_i(active.profile.ranks),
    .request_valid_i(request_valid_i && epochs_match),.request_ready_o(inline_ready),.request_i(request_i),
    .physical_valid_o(inline_physical_valid),.physical_ready_i(inline_physical_ready),.physical_o(inline_physical),
    .result_valid_i(backend_completion_valid && active.inline_ecc),.result_ready_o(inline_result_ready),.result_i(backend_completion),
    .completion_valid_o(inline_completion_valid),.completion_ready_i(completion_ready_i),.completion_o(inline_completion),
    .drained_o(inline_drained),.fault_o(inline_fault),.corrected_o(inline_corrected),.uncorrectable_o(inline_uncorrectable));
  lpddr5_mc_backend #(.DEPTH(DEPTH),.CHANNEL_INDEX(CHANNEL_INDEX)) backend (
    .clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.destructive_reset_i(destructive_reset),.fault_i(life_fault || config_fault || inline_fault || maintenance_fault || register_fault_q),
    .config_i(effective_config),.refresh_budget_i(refresh_budget),.maintenance_per_bank_i(maintenance_per_bank),.admission_i(active.inline_ecc ? !life_fault && !external_fault : host_admission),.issue_i(issue_enable),.service_i(service),.close_i(close_pages),.stop_i(stop_wck),
    .request_valid_i(backend_valid),.request_ready_o(backend_ready),.request_i(backend_request),
    .completion_valid_o(backend_completion_valid),.completion_ready_i(backend_completion_ready),.completion_o(backend_completion),
    .maintenance_valid_i(maintenance_valid),.maintenance_rank_i(maintenance_rank),.maintenance_rfm_i(maintenance_rfm),.maintenance_all_i(maintenance_all),.maintenance_pair_i(maintenance_pair),.maintenance_ready_o(maintenance_ready),
    .activate_allowed_i(activate_allowed),.register_pending_i(register_pending),.register_valid_i(register_valid),.register_rank_i(register_rank_q),.register_zq_i(register_zq_q),.register_address_i(7'd4),
    .register_ready_o(register_ready),.register_result_valid_o(register_result_valid),.register_result_rank_o(register_result_rank),.register_result_zq_o(register_result_zq),
    .register_result_failed_o(register_result_failed),.register_result_address_o(register_result_address),.register_result_value_o(register_result_value),
    .ca_o(backend_ca),.wck_o(wck_o),.write_o(write_o),.read_request_o(read_request_o),.return_i(return_i),.event_o(event_value),
    .drained_o(backend_drained),.pin_drained_o(pin_drained),.integrity_drained_o(integrity_drained),.pages_closed_o(pages_closed),.rows_closed_o(rows_closed),.wck_disabled_o(wck_disabled),.fault_o(backend_fault),
    .accepted_o(backend_accepted),.issued_o(issued),.replayed_o(replayed),.max_wait_o(scheduler_max_wait));
  lpddr5_mc_maintenance maintenance (
    .clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.destructive_reset_i(destructive_reset),.start_i(maintenance_start),.profile_i(active.profile),.per_bank_i(PER_BANK_REFRESH),.event_i(event_value),
    .pins_idle_i(pin_drained && rows_closed && wck_disabled),
    .temperature_valid_i(temperature_valid),.temperature_i(temperature),.self_refresh_enter_i(sr_enter),.self_refresh_exit_i(sr_exit),.external_refresh_i(external_refresh),.self_refresh_request_i(extra_service),
    .request_valid_o(maintenance_valid),.request_ready_i(maintenance_ready),.request_rank_o(maintenance_rank),.request_rfm_o(maintenance_rfm),.request_all_o(maintenance_all),.request_pair_o(maintenance_pair),
    .activate_allowed_o(activate_allowed),.urgent_o(maintenance_urgent),.fault_o(maintenance_fault),.extra_pending_o(extra_refresh),.refresh_budget_o(refresh_budget),.effective_per_bank_o(maintenance_per_bank),.effective_profile_o(effective_profile),
    .mr4_request_o(mr4_request),.zq_request_o(zq_request),.mr4_done_i(mr4_done),.zq_done_i(zq_done),.thermal_o(thermal),.diagnostic_o(maintenance_diagnostic));
  lpddr5_mc_update update_engine (
    .clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.destructive_reset_i(destructive_reset),.profile_i(effective_profile),.controller_request_i(update_request),
    .drained_i(quiescent && update_window && !fsp_busy),.wck_disabled_i(wck_disabled),.refresh_budget_i(refresh_budget),.controller_ack_i(management_i.ctrlupd_ack),
    .phy_request_i(management_i.phyupd_req),.phy_type_i(management_i.phyupd_type),.controller_request_o(ctrl_request),.phy_ack_o(phy_ack),.busy_o(update_busy),.done_o(update_done),.fault_o(update_fault));
  lpddr5_mc_fsp fsp_engine (
    .clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.destructive_reset_i(destructive_reset),.request_i(fsp_request && prepared_valid_o),.drained_i(quiescent),.wck_disabled_i(wck_disabled),.refresh_budget_i(refresh_budget),
    .current_profile_i(active.profile),.target_profile_i(prepared.profile),.active_fsp_i(active_fsp_q),.target_fsp_i(target_fsp_i),
    .current_frequency_i(active.profile.ratio==2 ? 5'd0 : 5'd1),.target_frequency_i(target_frequency_i),.active_epoch_i(committed_epoch_o),.target_epoch_i(prepared_epoch_o),
    .init_complete_i(management_i.init_complete),.init_start_o(fsp_init_start),.fsp_o(fsp_value),.ratio_o(fsp_ratio),.frequency_o(fsp_frequency),.ca_o(fsp_ca),
    .busy_o(fsp_busy),.declined_o(fsp_declined),.fault_o(fsp_fault),.clock_change_done_o(fsp_clock_done),.target_ready_o(fsp_target_ready),
    .publish_i(mailbox_i.commit && mailbox_i.destinations[CHANNEL_INDEX] && config_drained && prepared_valid_o),.publish_epoch_i(mailbox_i.epoch),.published_o(fsp_published),.frozen_target_o(frozen_target));
  lpddr5_mc_lifecycle lifecycle (
    .clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.cold_rst_ni,.profile_i(effective_profile),.fsp_target_profile_i(prepared.profile),.profile_valid_i(active_valid),.profile_epoch_i(committed_epoch_o),
    .command_valid_i(command_valid_i),.command_i(command_i),.command_ready_o(command_ready_o),.command_rejected_o(command_rejected_o),
    .host_settled_i(host_settled_i),.backend_drained_i(backend_drained),.integrity_drained_i(integrity_drained && inline_drained),.pages_closed_i(pages_closed),.wck_disabled_i(wck_disabled),
    .maintenance_urgent_i(maintenance_urgent),.extra_refresh_i(extra_refresh),.refresh_budget_i(refresh_budget),.fault_i(external_fault),.reset_confirmed_i(reset_confirmed_i),
    .train_ack_i(train_ack_i),.train_token_i(train_token_i),.management_i(management_i),.rank_report_valid_i(rank_report_valid_i),.rank_self_refresh_i(rank_self_refresh_i),.phy_refresh_done_i(phy_refresh_done_i),
    .update_busy_i(update_busy),.update_done_i(update_done),.update_fault_i(update_fault),.update_request_o(update_request),.update_window_o(update_window),
    .fsp_busy_i(fsp_busy),.fsp_done_i(fsp_published),.fsp_declined_i(fsp_declined),.fsp_fault_i(fsp_fault),.fsp_request_o(fsp_request),
    .admission_o(admission),.issue_o(issue_enable),.close_o(close_pages),.stop_o(stop_wck),.maintenance_enable_o(service),.destructive_reset_o(destructive_reset),.maintenance_start_o(maintenance_start),
    .self_refresh_enter_o(sr_enter),.self_refresh_exit_o(sr_exit),.external_refresh_o(external_refresh),.extra_service_o(extra_service),.ca_o(life_ca),
    .lp_ctrl_request_o(lp_ctrl_req),.lp_data_request_o(lp_data_req),.master_ack_o(master_ack),.syscs_state_o(syscs),.phy_refresh_request_o(phy_refresh_request),
    .state_o(life_state),.owner_o(owner),.fault_o(life_fault),.trusted_o(trusted),.train_request_o(train_request),.train_done_o(train_done),.boot_epoch_o(boot_epoch),.first_fault_o(first_fault),.fault_code_o(fault_code));
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if(!rst_ni) begin
      performance_q<='0;
      for(int s=0;s<DEPTH;s++) ledger_accepted_ck_q[s]<=0;
    end else begin
      performance_q.cycles<=saturating_add(performance_q.cycles,1);
      if(ca_o.cs!=0) performance_q.commands<=saturating_add(performance_q.commands,1);
      if(service && (maintenance_urgent || register_state_q!=REG_IDLE || maintenance_valid))
        performance_q.maintenance_ck<=saturating_add(performance_q.maintenance_ck,1);
      if(request_valid_i && request_ready_o && epochs_match && free_ledger>=0)
        ledger_accepted_ck_q[free_ledger]<=performance_q.cycles;
      if(completion_valid_o && completion_ready_i && done_ledger>=0) begin
        if(performance_q.cycles-ledger_accepted_ck_q[done_ledger]>performance_q.max_latency_ck)
          performance_q.max_latency_ck<=performance_q.cycles-ledger_accepted_ck_q[done_ledger];
        if(!completion_o.failed && completion_o.response==0 && completion_o.physical_done && completion_o.integrity_done) begin
          if(ledger_q[done_ledger].is_write)
            performance_q.payload_write<=saturating_add(performance_q.payload_write,64'($countones(ledger_q[done_ledger].is_64_bytes ? ledger_q[done_ledger].byte_enable : {32'd0,ledger_q[done_ledger].byte_enable[31:0]})));
          else performance_q.payload_read<=saturating_add(performance_q.payload_read,ledger_q[done_ledger].is_64_bytes ? 64'd64 : 64'd32);
        end
      end
    end
  end
  assign notification_events_o=notification.events;
  assign notification_error_valid_o=notification.error_valid;
  assign notification_error_o=notification.error;
  assign notification_boot_epoch_o=notification.boot_epoch;
  assign notification_config_epoch_o=notification.config_epoch;
  assign first_error_boot_epoch_o=first_error.boot_epoch;
  assign first_error_config_epoch_o=first_error.config_epoch;
  always_comb begin
    for(int p=0;p<3;p++) begin
      occurrences[p]='0;occurrences[p].boot_epoch=boot_epoch;occurrences[p].config_epoch=committed_epoch_o;
    end
    occurrences[0].events[0]=train_done && !train_done_q;
    occurrences[0].events[1]=train_request && !train_request_q;
    occurrences[0].events[2]=active_valid && (!config_valid_q || committed_epoch_o!=notification_epoch_q);
    occurrences[0].events[3]=life_state==1 && !quiescent_q;
    occurrences[0].events[4]=fsp_published;
    occurrences[0].events[8]=temperature_valid && temperature!=notification_temperature_q;
    // One summary per logical result. Inline decode pulses and the later
    // failed completion must never count the same error twice.
    if(completion_valid_o && completion_ready_i && done_ledger>=0 && (completion_o.corrected || completion_o.uncorrectable)) begin
      occurrences[1].events[5]=completion_o.corrected;occurrences[1].events[6]=completion_o.uncorrectable;
      occurrences[1].error_valid=1;occurrences[1].boot_epoch=completion_o.identity.boot_epoch;
      occurrences[1].config_epoch=completion_o.identity.config_epoch;
      occurrences[1].error.address=ledger_q[done_ledger].address;occurrences[1].error.axi_id=ledger_q[done_ledger].axi_id;
      occurrences[1].error.tag=ledger_q[done_ledger].tag;occurrences[1].error.port_index=ledger_q[done_ledger].port_index;
      occurrences[1].error.channel_index=1'(CHANNEL_INDEX);occurrences[1].error.correlated=1;
      occurrences[1].error.rank_index=ledger_q[done_ledger].rank_index;
      occurrences[1].error.operation=ledger_q[done_ledger].is_write ? LPDDR5_MC_WRITE : LPDDR5_MC_READ;
      occurrences[1].error.syndrome=completion_o.syndrome;occurrences[1].error.uncorrectable=completion_o.uncorrectable;
      occurrences[1].error.memory_trusted=trusted && !external_fault;
    end
    occurrences[2].events[9]=dfi_fault && !dfi_fault_q;
    occurrences[2].events[10]=maintenance_fault && !maintenance_fault_q;
    occurrences[2].events[12]=config_fault && !config_fault_q;
    occurrences[2].error_valid=|occurrences[2].events;
    occurrences[2].error.channel_index=1'(CHANNEL_INDEX);occurrences[2].error.uncorrectable=1;
    for(int p=0;p<3;p++) occurrence_valid[p]=|occurrences[p].events || occurrences[p].error_valid;
  end
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if(!rst_ni) begin
      train_request_q<=0;train_done_q<=0;config_valid_q<=0;quiescent_q<=0;notification_epoch_q<=0;
      config_fault_q<=0;dfi_fault_q<=0;maintenance_fault_q<=0;notification_temperature_q<=1;
    end else begin
      train_request_q<=train_request;train_done_q<=train_done;config_valid_q<=active_valid;quiescent_q<=life_state==1;
      notification_epoch_q<=committed_epoch_o;config_fault_q<=config_fault;dfi_fault_q<=dfi_fault;maintenance_fault_q<=maintenance_fault;
      if(temperature_valid) notification_temperature_q<=temperature;
    end
  end
  lpddr5_mc_notifications #(.DEPTH(NOTIFICATION_DEPTH)) notifications (
    .clk_ck_i(clk_ck_i),.rst_ni(cold_rst_ni),.destructive_reset_i(destructive_reset),
    .occurrence_valid_i(occurrence_valid),.occurrence_i(occurrences),
    .notification_valid_o(notification_valid_o),.notification_ready_i(notification_ready_i),.notification_o(notification),.overflow_o(notification_overflow),
    .first_error_clear_i(first_error_clear_i),.first_error_clear_version_i(first_error_clear_version_i),
    .first_error_valid_o(first_error_valid_o),.first_error_version_o(first_error_version_o),.first_error_o(first_error));
  // A nonempty backend needs a conservative drain allowance. After true pin,
  // page and WCK drain, reserve one register transaction only: REF may run
  // between ranks, so requiring an uninterrupted two-rank window starves MR4
  // at the shortest admitted thermal interval.
  assign register_reserve=pin_drained && pages_closed && wck_disabled
    ? register_service_budget(effective_profile)
    : 2*(32'(active.profile.rl_ck)+32'(active.profile.dram_ck_tras_min)+32'(active.profile.dram_ck_trp_ab)
    +(32'(active.profile.dfi_wck_tphy_rdlat)+32'(active.profile.wck_twck_dis)+8)/(active.profile.ratio==2 ? 2 : 4)
    +32'(active.profile.dram_ck_tmrr)+32'(active.profile.dram_ck_tmrw)+64);
  // Preserve the physical drain until every due register obligation retires,
  // including the IDLE/done edge between MR4 and an already-due ZQ sweep.
  // A completion pulse masks only its own stale request until the timer
  // samples it. Refresh may still preempt; transaction admission below keeps
  // its independent budget and urgency checks.
  assign register_pending=service && !life_fault && !external_fault
    && (register_state_q!=REG_IDLE || (mr4_request && !mr4_done) || (zq_request && !zq_done));
  assign register_valid=register_state_q==REG_SEND && service && !maintenance_urgent && !life_fault && !external_fault && refresh_budget>register_reserve;
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if(!rst_ni) begin
      register_state_q<=REG_IDLE;register_rank_q<=0;register_zq_q<=0;register_fault_q<=0;hottest_q<=1;
      temperature_valid<=0;temperature_sampled_q<=0;temperature<=9;mr4_done<=0;zq_done<=0;active_fsp_q<=0;
      accepted_q<=0;completed_q<=0;stale_q<=0;ledger_valid_q<=0;
      for(int s=0;s<DEPTH;s++) ledger_q[s]<='0;
    end else if(destructive_reset) begin
      register_state_q<=REG_IDLE;register_rank_q<=0;register_zq_q<=0;register_fault_q<=0;hottest_q<=1;
      temperature_valid<=0;temperature_sampled_q<=0;temperature<=9;mr4_done<=0;zq_done<=0;active_fsp_q<=0;ledger_valid_q<=0;
    end else begin
      temperature_valid<=0;mr4_done<=0;zq_done<=0;
      if(fsp_published) active_fsp_q<=fsp_value;
      if(request_valid_i && request_ready_o) begin
        if(!epochs_match) begin if(stale_q!='1) stale_q<=stale_q+1; end
        else begin
          ledger_valid_q[free_ledger]<=1;ledger_q[free_ledger]<=request_i;
          if(accepted_q!='1) accepted_q<=accepted_q+1;
        end
      end
      if(completion_valid_o && completion_ready_i) begin
        if(done_ledger<0) register_fault_q<=1;
        else begin
          ledger_valid_q[done_ledger]<=0;
          if(completed_q!='1) completed_q<=completed_q+1;
        end
      end
      case(register_state_q)
        // Completion reaches the maintenance timer on this edge. Its old
        // request is still visible until that timer samples done; do not
        // start another rank sweep from the just-completed obligation.
        REG_IDLE: if(service && !life_fault && !mr4_done && !zq_done && (mr4_request || zq_request)) begin
          register_state_q<=REG_SEND;register_rank_q<=0;register_zq_q<=!mr4_request;hottest_q<=1;
        end
        REG_SEND: if(register_valid && register_ready) register_state_q<=REG_WAIT;
        REG_WAIT: if(register_result_valid) begin
          if(register_result_failed || (!register_zq_q && (register_result_value[4:0]==0 || register_result_value[4:0]>15))) register_fault_q<=1;
          if(!register_zq_q && register_result_value[4:0]>hottest_q) hottest_q<=register_result_value[4:0];
          if(!register_rank_q && active.profile.ranks==2) begin register_rank_q<=1;register_state_q<=REG_SEND; end
          else begin
            register_state_q<=REG_IDLE;
            if(register_zq_q) zq_done<=1;
            else if(!register_fault_q && !register_result_failed && register_result_value[4:0] inside {[1:15]}) begin
              mr4_done<=1;temperature_valid<=1;temperature_sampled_q<=1;
              temperature<=register_result_value[4:0]>hottest_q ? register_result_value[4:0] : hottest_q;
            end
          end
        end
        default: register_fault_q<=1;
      endcase
      if(command_valid_i && command_ready_o && command_i==CH_CMD_FSP && !prepared_valid_o) register_fault_q<=1;
      if((backend_ca.cs!=0 && (life_ca.cs!=0 || fsp_ca.cs!=0)) || (life_ca.cs!=0 && fsp_ca.cs!=0)) register_fault_q<=1;
    end
  end
endmodule
