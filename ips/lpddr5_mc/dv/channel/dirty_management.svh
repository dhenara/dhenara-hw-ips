// Focused channel-input composition stimuli. No internal signal is forced.
// The wrapper only offers FSP after quiesced publication and power after host
// settlement; direct channel commands here check the owning drain invariant,
// not reachability through an APB command while that wrapper is busy.
bit dirty_observing=0;
int dirty_kind=0,dirty_fail=0,dirty_completions=0,dirty_entry_ck=-1;
logic dirty_original_done=0;
always @(posedge clk) if(dirty_observing) begin
  if(completion_valid && completion_ready) begin
    dirty_completions<=dirty_completions+1;
    assert(completion.identity.tag==request.tag && completion.identity.generation==request.generation
      && completion.identity.boot_epoch==request_boot && completion.identity.config_epoch==request_epoch
      && completion.physical_done && completion.integrity_done && !completion.failed)
      else $fatal(1,"dirty original completion prerequisites");
    dirty_original_done<=1;
  end
  if(management_out.ctrlupd_req || management_out.init_start || management_out.lp_ctrl_req || management_out.phymstr_ack) begin
    assert(dirty_original_done && host_settled && dut.inline_drained && status_value.ecc_drained
      && status_value.pin_drained && wck=='0)
      else $fatal(1,"dirty management entry before original/data/check drain");
    if(dirty_entry_ck<0) dirty_entry_ck<=cycles;
  end
  $display("DIRTY_OWNER kind=%0d failed=%0d ck=%0d inline_state=%0d data_committed=%0d inline_idle=%0d host_settled=%0d completed=%0d update=%0d fsp=%0d power=%0d master=%0d ca=%h wck=%h fault=%0d",
    dirty_kind,dirty_fail,cycles,dut.inline_ecc.state_q,dut.inline_ecc.data_committed_q,dut.inline_drained,
    host_settled,dirty_completions,management_out.ctrlupd_req,management_out.init_start,
    management_out.lp_ctrl_req,management_out.phymstr_ack,ca,wck,status_value.fault);
end

task automatic dirty_management_case(input int kind,input bit fail_dirty);
  lpddr5_mc_channel_config_t target_config;
  int request_edge,release_edge;
  c2_begin(kind,fail_dirty);
  setup_control(1,1);
  if(kind==1) begin
    send_command(CH_CMD_QUIESCE);wait_quiet();
    cfg.profile=fixture_profile(1,0,0,1,0,1);cfg.profile.ranks=1;cfg.config_epoch=2;
    target_config=cfg;
    config_mailbox='0;config_mailbox.destinations=1;config_mailbox.epoch=2;
    config_mailbox.payload_crc=config_crc(cfg);config_mailbox.prepare=1;step();config_mailbox.prepare=0;
    assert(prepared && !prepare_error && committed_epoch==1) else $fatal(1,"dirty FSP immutable preparation");
    enable_after_maintenance();
  end
  request=granule(0,1);request.byte_enable='1;request_valid=1;
  for(int c=0;c<2000;c++) begin
    #1;if(request_ready) begin tick();request_valid=0;break;end
    step();
  end
  assert(!request_valid) else $fatal(1,"dirty request admission");
  host_settled=0;completion_ready=0;
  for(int c=0;c<3000;c++) begin
    step();if(dut.inline_ecc.data_committed_q) break;
  end
  assert(dut.inline_ecc.data_committed_q && !dut.inline_drained && !status_value.ecc_drained)
    else $fatal(1,"dirty data/check interval never exercised");
  dirty_kind=kind;dirty_fail=int'(fail_dirty);dirty_completions=0;dirty_original_done=0;dirty_entry_ck=-1;dirty_observing=1;
  case(kind)
    0: send_command(CH_CMD_UPDATE);
    1: send_command(CH_CMD_FSP);
    2: send_command(CH_CMD_POWER_DOWN);
    3: begin
      management_in.phymstr_req=1;management_in.phymstr_type=0;
      management_in.phymstr_state_sel=1;management_in.phymstr_cs_state=1;
    end
    default: $fatal(1,"dirty management kind");
  endcase
  if(fail_dirty) begin
    // Error occurs after physical data and before the check result. No
    // successful original retirement or management ownership is permitted.
    management_in.error=1;
    for(int c=0;c<20;c++) begin tick();if(status_value.fault) break;end
    assert(status_value.fault && !status_value.memory_trusted && !request_ready && dirty_entry_ck<0)
      else $fatal(1,"dirty management failure did not fence trust/admission");
    assert(dirty_completions==0) else $fatal(1,"success from unoffered dirty failure");
    dirty_observing=0;management_in.error=0;
    // The channel has no surviving AXI ledger in this fixture. Explicitly
    // attest settlement/reset, verify epoch invalidation; wrapper settlement
    // remains covered separately and is not inferred from this input.
    host_settled=1;reset_confirmed=1;send_command(CH_CMD_RECOVER);memory_reset=1;tick();memory_reset=0;
    assert(status_value.boot_epoch==1 && !status_value.config_valid && !status_value.memory_trusted)
      else $fatal(1,"dirty management failure recovery epoch");
  end else begin
    // Hold the original completion to prove an idle physical backend alone
    // cannot authorize management while the inline lock remains reserved.
    for(int c=0;c<3000;c++) begin
      step();if(completion_valid) break;
    end
    assert(completion_valid && !completion_ready) else $fatal(1,"dirty held completion never offered");
    repeat(8) step();
    assert(dirty_entry_ck<0 && dirty_completions==0) else $fatal(1,"dirty held response lost dependency");
    completion_ready=1;
    for(int c=0;c<3000;c++) begin
      step();if(dirty_original_done) break;
    end
    assert(dirty_completions==1) else $fatal(1,"missing/duplicate dirty original completion");
    host_settled=1;
    for(int c=0;c<2000;c++) begin step();if(dirty_entry_ck>=0) break;end
    assert(dirty_entry_ck>=0) else $fatal(1,"dirty management entry never exercised");
    request_edge=cycles;release_edge=-1;
    if(kind==0) begin
      // Exact early sampled ACK, then release within the frozen profile.
      management_in.ctrlupd_ack=1;step();management_in.ctrlupd_ack=0;wait_quiet();
    end else if(kind==1) begin
      management_in.init_complete=0;
      for(int c=0;c<1600;c++) begin
        if(!management_out.init_start && release_edge<0) release_edge=cycles;
        if(release_edge>=0 && cycles>=release_edge+2) management_in.init_complete=1;
        step();if(fsp_ready) break;
      end
      assert(fsp_ready && release_edge>request_edge) else $fatal(1,"dirty FSP completion");
      cfg=target_config;config_mailbox.commit=1;step();config_mailbox='0;memory_profile=cfg.profile;request_epoch=2;wait_quiet();
    end else if(kind==2) begin
      management_in.lp_ctrl_ack=1;management_in.lp_data_ack=1;
      repeat(12) step();send_command(CH_CMD_WAKE);
      management_in.lp_ctrl_ack=0;management_in.lp_data_ack=0;wait_quiet();
    end else begin
      report_valid=0;step();report_sr=0;report_valid=1;
      repeat(4) step();management_in.phymstr_req=0;wait_quiet();
    end
    assert(dirty_completions==1 && !status_value.fault && status_value.memory_trusted)
      else $fatal(1,"dirty management terminal settlement");
    dirty_observing=0;
  end
  // Retain the terminal pre-edge state, including reset epoch invalidation.
  tick();
  c2_end();
  $display("PASS dirty management composition kind=%0d failure=%0d",kind,fail_dirty);
endtask
