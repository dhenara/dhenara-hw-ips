// Actual consumed CK controls and mailbox identities, before NBA. Full images
// are captured at their transfer; no record is synthesized from a marker.
for(genvar c=0;c<CHANNELS;c++) begin : wf_observers
  // Settle the descriptor after the actual commit NBA and before the next
  // falling prepare. This single writer avoids a same-delta module-input
  // propagation race; the memory instance and its decoded bytes remain live.
  initial forever begin : memory_profile_commit
    bit consumed_commit;
    logic [31:0] captured_epoch;
    int captured_slot;
    @(posedge clk);
    consumed_commit=wf_case>=0 && dut.mailbox_value[c].commit;
    captured_epoch=dut.mailbox_value[c].epoch;captured_slot=int'(dut.target_fsp[c]);
    #0.001;
    if(wf_case>=0) begin
      if(memory_reset || dut.datapath.channels[c].channel.destructive_reset)
        wf_memory_profile[c]=qualified[0];
      else if(consumed_commit) begin
        check(captured_slot inside {0,1,2} && status_value[c].config_valid
          && status_value[c].config_epoch==captured_epoch
          && dut.datapath.channels[c].channel.active.profile==qualified[captured_slot],
          "FSP memory descriptor lacks actual committed frozen profile");
        check(memories[c].memory_model.command_count==0 && !memories[c].memory_model.act_pending
          && memories[c].memory_model.open_banks==0 && memories[c].memory_model.return_o.valid==0,
          "FSP fixture profile change before independent command/pair/return/row drain");
        wf_memory_profile[c]=qualified[captured_slot];
      end
    end
  end
  task automatic capture_fsp;
    $fdisplay(wm_stream,"{\"kind\":\"fsp_edge\",\"ps\":%0d,\"tick\":%0d,\"channel\":%0d,\"start\":%0d,\"complete\":%0d,\"ratio\":%0d,\"frequency\":%0d,\"fsp\":%0d,\"clock_done\":%0d,\"wck_disabled\":%0d,\"ready\":%0d,\"declined\":%0d,\"fault\":%0d,\"prepared\":%0d,\"prepared_epoch\":%0d,\"epoch\":%0d,\"boot\":%0d,\"active_image\":\"0x%h\",\"prepared_image\":\"0x%h\",\"memory_image\":\"0x%h\",\"mb_prepare\":%0d,\"mb_commit\":%0d,\"mb_abort\":%0d,\"mb_epoch\":%0d,\"mb_crc\":%0d,\"mb_dest\":%0d,\"config_crc\":%0d,\"config_image\":\"0x%h\",\"target_fsp\":%0d}",
      observer.timestamp_ps(),
      wm_tick[c]-1,
      c,
      management_out[c].init_start,
      management[c].init_complete,
      management_out[c].frequency_ratio,
      management_out[c].frequency,
      management_out[c].fsp,
      dut.datapath.channels[c].channel.fsp_clock_done,
      dut.datapath.channels[c].channel.wck_disabled,
      dut.fsp_ready[c],
      dut.fsp_declined[c],
      dut.datapath.channels[c].channel.fsp_fault,
      dut.prepared[c],
      dut.prepared_epoch[c],
      dut.committed_epoch[c],
      status_value[c].boot_epoch,
      dut.datapath.channels[c].channel.active.profile,
      dut.datapath.channels[c].channel.prepared.profile,
      wf_memory_profile[c],
      dut.mailbox_value[c].prepare,
      dut.mailbox_value[c].commit,
      dut.mailbox_value[c].abort_request,
      dut.mailbox_value[c].epoch,
      dut.mailbox_value[c].payload_crc,
      dut.mailbox_value[c].destinations,
      lpddr5_mc_channel_pkg::config_crc(dut.channel_config[c]),
      dut.channel_config[c].profile,
      dut.target_fsp[c]);
    if(dut.c_config_valid[c] && dut.c_config_ready[c]) begin
      $fwrite(wm_stream,"{\"kind\":\"fsp_transfer\",\"ps\":%0d,\"tick\":%0d,\"channel\":%0d,\"sequence\":%0d,\"boot\":%0d,\"prepare\":%0d,\"commit\":%0d,\"abort\":%0d,\"dest\":%0d,\"crc\":%0d,\"image\":\"0x",
        observer.timestamp_ps(),
        wm_tick[c]-1,
        c,
        dut.c_config[c].publication.epoch,
        dut.c_config[c].boot_epoch,
        dut.c_config[c].publication.prepare,
        dut.c_config[c].publication.commit,
        dut.c_config[c].publication.abort_request,
        dut.c_config[c].publication.destinations,
        dut.c_config[c].publication.payload_crc);
      // Emit the complete register image in bounded words. This preserves
      // every bit without relying on a simulator-specific wide format limit.
      for(int word_index=REG_COUNT-1;word_index>=0;word_index--) $fwrite(wm_stream,"%08h",dut.c_config[c].image[word_index]);
      $fdisplay(wm_stream,"\"}");
    end
    if(dut.c_ack_valid[c] && dut.c_ack_ready[c]) begin
      $fdisplay(wm_stream,"{\"kind\":\"fsp_ack\",\"ps\":%0d,\"tick\":%0d,\"channel\":%0d,\"sequence\":%0d,\"boot\":%0d,\"phase\":%0d,\"accepted\":%0d}",
        observer.timestamp_ps(),
        wm_tick[c]-1,
        c,
        dut.c_ack[c].sequence_number,
        dut.c_ack[c].boot_epoch,
        dut.c_ack[c].phase,
        dut.c_ack[c].accepted);
    end
  endtask
end
always @(posedge pclk) if(wm_stream!=0 && wf_case>=0) begin
  $fdisplay(wm_stream,"{\"kind\":\"fsp_apb\",\"ps\":%0d,\"psel\":%0d,\"penable\":%0d,\"pready\":%0d,\"write\":%0d,\"address\":%0d,\"data\":%0d,\"strobe\":%0d,\"error\":%0d,\"response\":%0d,\"settled\":%0d,\"quiesced\":%0d,\"enabled\":%0d,\"valid\":%0d,\"epoch\":%0d,\"sequence\":%0d,\"busy\":%0d,\"boot\":%0d}",
    observer.timestamp_ps(),
    psel,
    penable,
    pready,
    apb_request.write,
    apb_request.address,
    apb_request.data,
    apb_request.strobe,
    apb_response.error,
    apb_response.data,
    dut.global_pq.settled,
    dut.global_pq.quiesced,
    dut.global_pq.enabled,
    dut.apb_valid,
    dut.apb_epoch,
    dut.apb.sequence_q,
    dut.apb_busy,
    dut.publication_boot);
  if(psel && penable && pready && apb_request.write && !apb_response.error
      && ((apb_request.address==18'(REG_OFFSET[INDEX_CONFIG_COMMIT]) && apb_request.data[0])
      || (apb_request.address==18'(REG_OFFSET[INDEX_DVFS]) && apb_request.data[8])))
    for(int c=0;c<CHANNELS;c++) begin
      $fwrite(wm_stream,"{\"kind\":\"fsp_freeze\",\"ps\":%0d,\"channel\":%0d,\"sequence\":%0d,\"boot\":%0d,\"image\":\"0x",
        observer.timestamp_ps(),
        c,
        dut.apb.sequence_q+1,
        dut.apb.boot_d);
      for(int word_index=REG_COUNT-1;word_index>=0;word_index--) $fwrite(wm_stream,"%08h",dut.apb.frozen_d[c][word_index]);
      $fdisplay(wm_stream,"\"}");
    end
  for(int c=0;c<CHANNELS;c++) if(dut.p_ack_valid[c] && dut.p_ack_ready[c]) begin
    $fdisplay(wm_stream,"{\"kind\":\"fsp_apb_ack\",\"ps\":%0d,\"channel\":%0d,\"sequence\":%0d,\"boot\":%0d,\"phase\":%0d,\"accepted\":%0d}",
      observer.timestamp_ps(),
      c,
      dut.p_ack[c].sequence_number,
      dut.p_ack[c].boot_epoch,
      dut.p_ack[c].phase,
      dut.p_ack[c].accepted);
  end
end
