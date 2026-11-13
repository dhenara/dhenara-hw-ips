// Per-channel passive maintenance/lifecycle witnesses and fixture admission.
`define WM_CH dut.datapath.channels[c].channel
  wire phase_reset_ok=rst_n && !memory_reset && !`WM_CH.destructive_reset;
  task automatic invalidate_readiness;
    wm_phase_invalidate(c);
    wm_ref_tick[c]=-1;wm_mrr_tick[c]=-1;wm_zq_tick[c]=-1;wm_mr4_complete[c]=0;
  endtask
  // Coroutines preserve even a reset pulse between sampled edges. Epoch
  // changes also discard command-origin and return-completion eligibility.
  initial forever begin @(negedge phase_reset_ok);invalidate_readiness();end
  initial forever begin
    @(status_value[c].boot_epoch or status_value[c].config_epoch);invalidate_readiness();
  end
  function automatic bit diagnostic_clear();
    // Match every current veto in MaintenanceWindow.check. first_fault is
    // historical evidence and is intentionally absent from this predicate.
    return `WM_CH.maintenance.running_q && `WM_CH.thermal==9
      && !(|{`WM_CH.maintenance_urgent,`WM_CH.maintenance.pending_q,`WM_CH.maintenance.candidate,
        `WM_CH.maintenance.mr4_pending_q,`WM_CH.maintenance.zq_pending_q,`WM_CH.mr4_request,`WM_CH.zq_request,
        `WM_CH.maintenance.thermal_debt_q,`WM_CH.extra_refresh,`WM_CH.register_pending,`WM_CH.register_state_q,
        `WM_CH.backend.register_owner_q,`WM_CH.maintenance_valid,`WM_CH.register_valid,`WM_CH.register_result_valid,
        `WM_CH.lifecycle.failure_code,`WM_CH.fault_code,`WM_CH.lifecycle.master_pending_q,`WM_CH.external_fault,
        `WM_CH.update_fault,`WM_CH.fsp_fault,`WM_CH.backend_fault,`WM_CH.inline_fault,`WM_CH.register_fault_q,
        `WM_CH.maintenance_fault,`WM_CH.backend.registers.state_q});
  endfunction
  task automatic observe_commands;
    if(!rst_n || memory_reset || `WM_CH.destructive_reset) begin
      wm_ref_tick[c]=-1;wm_mrr_tick[c]=-1;wm_zq_tick[c]=-1;wm_mr4_complete[c]=0;
    end else if(ca[c].cs!=0) begin
      if(ca[c].address==14'h2038) wm_ref_tick[c]=64'(wm_tick[c]);
      if(ca[c].address==14'h0218) begin wm_mrr_tick[c]=64'(wm_tick[c]);wm_mr4_complete[c]=0;end
      if(ca[c].address==14'h0370) wm_zq_tick[c]=64'(wm_tick[c]);
    end
  endtask
  // The fixture may use the actual successful register result to wait.
  // Replay must independently recover all16 MRR slices before accepting it.
  initial forever begin
    @(posedge clk);if(WRAPPER_MANAGEMENT && `WM_CH.register_result_valid
      && !`WM_CH.register_result_failed && !`WM_CH.register_result_zq
      && `WM_CH.register_result_address==4 && `WM_CH.register_result_value==9)
    wm_mr4_complete[c]=1;
  end
  function automatic bit ready(input int required_ck,input bit idle);
    bit live_eligible;
    live_eligible=wm_ref_tick[c]>=0 && wm_mrr_tick[c]>=0 && wm_zq_tick[c]>=0 && wm_mr4_complete[c]
      && 64'(wm_tick[c])+64'(required_ck)<wm_ref_tick[c]+3124-64'(WM_WINDOW_REFRESH_LEAD_CK)
      && 64'(wm_tick[c])+64'(required_ck)<wm_mrr_tick[c]+3000-64'(WM_WINDOW_POLL_LEAD_CK)
      && 64'(wm_tick[c])+64'(required_ck)<wm_zq_tick[c]+3000-64'(WM_WINDOW_POLL_LEAD_CK)
      && `WM_CH.thermal==9 && mr4_value[c]==9 && `WM_CH.maintenance.running_q
      && !`WM_CH.maintenance_urgent && !`WM_CH.maintenance_valid && !`WM_CH.register_pending
      && !`WM_CH.mr4_request && !`WM_CH.zq_request && `WM_CH.maintenance.thermal_debt_q==0
      && !`WM_CH.extra_refresh && !status_value[c].fault
      && status_value[c].config_valid && status_value[c].memory_trusted
      && enable && !management[c].phyupd_req && !management[c].phymstr_req
      && diagnostic_clear() && (!idle || (settled && status_value[c].drained));
    return wm_phase_ready(c,wm_tick[c]-1,observer.timestamp_ps(),phase_reset_ok,
      status_value[c].boot_epoch,status_value[c].config_epoch,live_eligible);
  endfunction
  task automatic snapshot(input string kind,input int tick);
    wm_phase_record(c,kind=="window_edge",tick,observer.timestamp_ps(),phase_reset_ok,
      status_value[c].boot_epoch,status_value[c].config_epoch,diagnostic_clear());
    $fdisplay(wm_stream,"{\"kind\":\"%s\",\"ps\":%0d,\"channel\":%0d,\"tick\":%0d,\"life_failure\":%0d,\"life_fault_code\":%0d,\"life_first_fault\":%0d,\"master_pending\":%0d,\"master_age\":%0d,\"master_elapsed\":%0d,\"life_elapsed\":%0d,\"external_fault\":%0d,\"update_fault\":%0d,\"fsp_fault\":%0d,\"backend_fault\":%0d,\"inline_fault\":%0d,\"register_fault\":%0d,\"maintenance_fault\":%0d,\"maint_ck\":%0d,\"running\":%0d,\"urgent\":%0d,\"pending\":%0d,\"candidate\":%0d,\"poll_refresh\":%0d,\"mr4_pending\":%0d,\"zq_pending\":%0d,\"mr4_request\":%0d,\"zq_request\":%0d,\"mr4_done\":%0d,\"zq_done\":%0d,\"thermal\":%0d,\"thermal_debt\":%0d,\"thermal_deadline\":%0d,\"extra_pending\":%0d,\"last_refresh\":%0d,\"last_mr4\":%0d,\"last_zq\":%0d,\"refresh_budget\":%0d,\"refresh_lead\":%0d,\"poll_lead\":%0d,\"reserve\":%0d,\"maintenance_valid\":%0d,\"maintenance_ready\":%0d,\"maintenance_rfm\":%0d,\"maintenance_all\":%0d,\"register_pending\":%0d,\"register_state\":%0d,\"register_owner\":%0d,\"register_valid\":%0d,\"register_ready\":%0d,\"register_result_valid\":%0d,\"register_result_failed\":%0d,\"register_result_zq\":%0d,\"register_result_address\":%0d,\"register_result_value\":%0d,\"register_io_state\":%0d}",
      kind,observer.timestamp_ps(),c,tick,
      `WM_CH.lifecycle.failure_code,`WM_CH.fault_code,`WM_CH.first_fault,`WM_CH.lifecycle.master_pending_q,
      `WM_CH.lifecycle.master_age_q,`WM_CH.lifecycle.master_elapsed_q,`WM_CH.lifecycle.elapsed_q,`WM_CH.external_fault,
      `WM_CH.update_fault,`WM_CH.fsp_fault,`WM_CH.backend_fault,`WM_CH.inline_fault,
      `WM_CH.register_fault_q,`WM_CH.maintenance_fault,`WM_CH.maintenance.ck_q,`WM_CH.maintenance.running_q,
      `WM_CH.maintenance_urgent,`WM_CH.maintenance.pending_q,`WM_CH.maintenance.candidate,`WM_CH.maintenance.poll_refresh_q,
      `WM_CH.maintenance.mr4_pending_q,`WM_CH.maintenance.zq_pending_q,`WM_CH.mr4_request,`WM_CH.zq_request,
      `WM_CH.mr4_done,`WM_CH.zq_done,`WM_CH.thermal,`WM_CH.maintenance.thermal_debt_q,
      `WM_CH.maintenance.thermal_deadline_q,`WM_CH.extra_refresh,`WM_CH.maintenance.last_refresh_q[0],`WM_CH.maintenance.last_mr4_q,
      `WM_CH.maintenance.last_zq_q,`WM_CH.refresh_budget,`WM_CH.maintenance.refresh_lead,`WM_CH.maintenance.poll_lead,
      `WM_CH.maintenance.reserve_value,`WM_CH.maintenance_valid,`WM_CH.maintenance_ready,`WM_CH.maintenance_rfm,
      `WM_CH.maintenance_all,`WM_CH.register_pending,`WM_CH.register_state_q,`WM_CH.backend.register_owner_q,
      `WM_CH.register_valid,`WM_CH.register_ready,`WM_CH.register_result_valid,`WM_CH.register_result_failed,
      `WM_CH.register_result_zq,`WM_CH.register_result_address,`WM_CH.register_result_value,`WM_CH.backend.registers.state_q);
  endtask
`undef WM_CH
