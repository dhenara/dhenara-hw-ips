// APB-domain staging and publication owner. Stream inputs are coherent payloads
// already crossed into pclk; no multibit signal here may use bit synchronizers.
module lpddr5_mc_apb #(
  parameter int CHANNELS = 2,
  parameter int PORTS = 2,
  parameter int RANKS = 1,
  parameter int AXI_BITS = 128,
  parameter int ADDRESS_BITS = 40,
  parameter int QUEUE_DEPTH = 64,
  parameter int COMMAND_DEPTH = 8,
  parameter int PUBLICATION_TIMEOUT = 4096
) (
  input logic pclk,
  input logic preset_n,
  input logic cold_preset_n,
  input logic psel_i,
  input logic penable_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_apb_request_t request_i,
  output logic pready_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_apb_response_t response_o,
  input logic enabled_i,
  input logic quiesced_i,
  input logic host_settled_i,
  input logic [1:0] active_fsp_i,
  input logic link_fault_i,
  // Assert only after the channel owner has confirmed destructive recovery.
  input logic recovery_confirmed_i,
  input logic global_status_valid_i,
  input lpddr5_mc_apb_metadata_pkg::lpddr5_mc_register_image_t global_status_i,
  output logic global_status_ready_o,
  input logic [CHANNELS-1:0] status_valid_i,
  input lpddr5_mc_apb_pkg::lpddr5_mc_channel_status_t [CHANNELS-1:0] status_i,
  output logic [CHANNELS-1:0] status_ready_o,
  output logic [CHANNELS-1:0] config_valid_o,
  input logic [CHANNELS-1:0] config_ready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_config_mailbox_t [CHANNELS-1:0] config_o,
  output lpddr5_mc_apb_metadata_pkg::lpddr5_mc_register_image_t [CHANNELS-1:0] config_image_o,
  output logic [31:0] config_boot_epoch_o,
  input logic [CHANNELS-1:0] config_ack_valid_i,
  input lpddr5_mc_apb_pkg::lpddr5_mc_publication_ack_t [CHANNELS-1:0] config_ack_i,
  output logic [CHANNELS-1:0] config_ack_ready_o,
  output logic command_valid_o,
  input logic command_ready_i,
  output lpddr5_mc_apb_pkg::lpddr5_mc_control_command_t command_o,
  output logic [2:0] last_protection_o,
  output logic irq_o,
  output logic configuration_valid_o,
  output logic fault_o,
  output logic publication_busy_o,
  output logic [31:0] configuration_epoch_o,
  output lpddr5_mc_apb_metadata_pkg::lpddr5_mc_register_image_t [CHANNELS-1:0] active_image_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_apb_metadata_pkg::*;
  import lpddr5_mc_apb_pkg::*;

  typedef enum logic [2:0] {IDLE, HASH_IMAGE, PREPARE, COMMIT, ABORT} state_t;
  state_t state_q, state_d;
  lpddr5_mc_register_image_t [CHANNELS-1:0] staging_q, staging_d;
  lpddr5_mc_register_image_t [CHANNELS-1:0] frozen_q, frozen_d;
  lpddr5_mc_register_image_t [CHANNELS-1:0] active_q, active_d;
  lpddr5_mc_control_command_t [COMMAND_DEPTH-1:0] commands_q, commands_d;
  int unsigned head_q, head_d, tail_q, tail_d, count_q, count_d;
  int unsigned timer_q, timer_d, hash_index_q, hash_index_d;
  logic [CHANNELS-1:0][31:0] crc_q, crc_d;
  logic [CHANNELS-1:0] sent_q, sent_d, acked_q, acked_d;
  logic [CHANNELS-1:0] declined_q, declined_d;
  logic prior_valid_q, prior_valid_d;
  logic [CHANNELS-1:0] first_error_q, first_error_d;
  logic [CHANNELS-1:0][12:0] first_events_q, first_events_d;
  logic [31:0] sequence_q, sequence_d, epoch_q, epoch_d, boot_q, boot_d;
  logic [12:0] irq_q, irq_d;
  logic [31:0] hardware_state_q, hardware_state_d;
  logic valid_q, valid_d, fault_q, fault_d, access_q, access_d;
  logic diagnostic_active_q;

  function automatic lpddr5_mc_register_image_t reset_image();
    lpddr5_mc_register_image_t result;
    for (int r = 0; r < REG_COUNT; r++) result[r] = REG_RESET[r];
    result[INDEX_CONFIG] = 32'(PORTS) | (32'(CHANNELS) << 4) |
        (32'(RANKS) << 8) | (32'(AXI_BITS / 8) << 12);
    return result;
  endfunction
  localparam lpddr5_mc_register_image_t RESET_IMAGE = reset_image();

  function automatic logic [31:0] crc_word(input logic [31:0] crc, input logic [31:0] word_value);
    logic [31:0] value;
    value = crc;
    for (int bit_index = 0; bit_index < 32; bit_index++) begin
      if (value[0] ^ word_value[bit_index]) value = (value >> 1) ^ 32'hedb88320;
      else value = value >> 1;
    end
    return value;
  endfunction

  // Structural controller checks complement, rather than replace, each
  // channel's full validation of its numeric PHY profile and real drain.
  function automatic logic image_valid(input lpddr5_mc_register_image_t image);
    logic result;
    logic [63:0] bases [8];
    logic [63:0] sizes [8];
    logic [63:0] total, per_rank;
    logic [39:0] used;
    int width;
    int source;
    result = 1'b1;
    total = 0;
    if (!(image[INDEX_MODE][10:8] inside {3'd1, 3'd2, 3'd4})) result = 1'b0;
    if (image[INDEX_AGE_STEP] != 1 || image[INDEX_AGE_TICK_DIV] == 0) result = 1'b0;
    if (image[INDEX_WR_LOW] > image[INDEX_WR_HIGH] || image[INDEX_WR_HIGH] > 32'(QUEUE_DEPTH)) result = 1'b0;
    if (image[INDEX_RD_BATCH_MIN] > image[INDEX_WR_BATCH_MAX] ||
        image[INDEX_WR_BATCH_MIN] > image[INDEX_WR_BATCH_MAX]) result = 1'b0;
    if (image[INDEX_BW_MODE] > 1 || image[INDEX_PM_REQUEST] > 2) result = 1'b0;
    if (image[INDEX_FSP_VALID] == 0) result = 1'b0;
    if (image[INDEX_PORT_LIMIT_0][7:0] > 8'(QUEUE_DEPTH) ||
        image[INDEX_PORT_LIMIT_0][15:8] > 8'(QUEUE_DEPTH)) result = 1'b0;
    if (PORTS == 2 && (image[INDEX_PORT_LIMIT_1][7:0] > 8'(QUEUE_DEPTH) ||
        image[INDEX_PORT_LIMIT_1][15:8] > 8'(QUEUE_DEPTH))) result = 1'b0;
    for (int r = 0; r < 8; r++) begin
      bases[r] = {24'b0, image[REGION_BASE_HI_INDEX[r]][7:0], image[REGION_BASE_LO_INDEX[r]]};
      sizes[r] = {32'b0, image[REGION_SIZE_4K_INDEX[r]]} << 12;
      if (image[REGION_ATTR_INDEX[r]][0]) begin
        if (sizes[r] < 4096 || (sizes[r] & (sizes[r] - 1)) != 0 || bases[r][11:0] != 0 ||
            bases[r] + sizes[r] > (64'd1 << ADDRESS_BITS)) result = 1'b0;
        if (image[REGION_ATTR_INDEX[r]] != (32'd1 | (CHANNELS == 2 ? 32'd4 : 32'd0) |
            (32'(RANKS) << 3) | (32'(image[INDEX_MODE][1]) << 5))) result = 1'b0;
        if (image[REGION_MAP_INDEX[r]] != 1) result = 1'b0;
        width = 0;
        for (int bit_index = 0; bit_index < 40; bit_index++)
          if ((64'd1 << bit_index) < sizes[r]) width = bit_index + 1;
        used = 0;
        for (int bit_index = 0; bit_index < 40; bit_index++) begin
          source = int'(image[REGION_BIT_INDEX[r][bit_index]][5:0]);
          if (bit_index < width) begin
            if (source >= width || source >= 40) result = 1'b0;
            else begin
              if (used[source]) result = 1'b0;
              used[source] = 1'b1;
            end
            if (bit_index < 6 && source != bit_index) result = 1'b0;
          end
        end
        for (int other = 0; other < r; other++) begin
          if (image[REGION_ATTR_INDEX[other]][0] && bases[r] < bases[other] + sizes[other] &&
              bases[other] < bases[r] + sizes[r]) result = 1'b0;
        end
        total += sizes[r];
      end
    end
    per_rank = image[INDEX_MODE][1] ? ((64'd1073741824 / 576) * 512) : 64'd1073741824;
    if (total == 0 || total > per_rank * 64'(RANKS) * 64'(CHANNELS)) result = 1'b0;
    return result;
  endfunction

  assign active_image_o = active_q;
  assign configuration_epoch_o = epoch_q;
  assign configuration_valid_o = valid_q;
  assign fault_o = fault_q;
  assign publication_busy_o = state_q != IDLE;
  assign config_image_o = frozen_q;
  assign config_boot_epoch_o = boot_q;
  assign command_valid_o = count_q != 0;
  assign command_o = commands_q[head_q];
  assign irq_o = |(irq_q & staging_q[0][INDEX_IRQ_MASK][12:0]);
  assign status_ready_o = {CHANNELS{diagnostic_active_q}};
  assign global_status_ready_o = 1'b1;
  assign config_ack_ready_o = '1;

  always_comb begin : next_state
    int register_index;
    int channel_index;
    int needed;
    logic shared_access, locked, bad_access, broadcast;
    logic [31:0] byte_mask, command_mask, command_payload;
    logic [12:0] clear_events, set_events;
    logic [31:0] current_boot;
    logic [CHANNELS-1:0] ack_seen;
    logic bad_ack;
    logic access_fire;
    logic snapshot_ok,publication_request;
    lpddr5_mc_register_image_t [CHANNELS-1:0] candidate_image;
    state_d = state_q;
    staging_d = staging_q;
    frozen_d = frozen_q;
    active_d = active_q;
    commands_d = commands_q;
    head_d = head_q;
    tail_d = tail_q;
    count_d = count_q;
    timer_d = timer_q;
    hash_index_d = hash_index_q;
    crc_d = crc_q;
    sent_d = sent_q;
    acked_d = acked_q;
    declined_d = declined_q;
    prior_valid_d = prior_valid_q;
    first_error_d = first_error_q;
    first_events_d = first_events_q;
    sequence_d = sequence_q;
    epoch_d = epoch_q;
    boot_d = boot_q;
    irq_d = irq_q;
    hardware_state_d = hardware_state_q;
    valid_d = valid_q;
    fault_d = fault_q;
    access_d = access_q;
    response_o = '0;
    config_valid_o = '0;
    config_o = '0;
    register_index = -1;
    channel_index = 0;
    needed = 0;
    locked = 1'b0;
    broadcast = 1'b0;
    shared_access = request_i.address < 18'h10000;
    byte_mask = 0;
    command_mask = 0;
    command_payload = 0;
    clear_events = 0;
    set_events = 0;
    current_boot = staging_q[0][INDEX_BOOT_EPOCH];
    ack_seen = acked_q;
    bad_ack = 1'b0;
    access_fire = 1'b0;
    snapshot_ok = 1'b1;
    publication_request = 1'b0;candidate_image = staging_q;
    if (!shared_access) channel_index = int'(request_i.address[17:16]) - 1;
    for (int r = 0; r < REG_COUNT; r++) begin
      if (REG_SHARED[r] == shared_access && REG_OFFSET[r] == request_i.address[15:0]) register_index = r;
    end
    bad_access = register_index < 0 || channel_index < 0 || channel_index >= CHANNELS ||
        request_i.address[1:0] != 0 || (!request_i.write && request_i.strobe != 0);
    for (int lane = 0; lane < 4; lane++)
      if (request_i.strobe[lane]) byte_mask[lane * 8 +: 8] = 8'hff;
    if (register_index >= 0 && channel_index >= 0 && channel_index < CHANNELS) begin
      broadcast = request_i.write && !shared_access && staging_q[0][INDEX_CONTROL][4];
      if (broadcast && channel_index != 0) bad_access = 1'b1;
      locked = enabled_i && ((REG_LOCK[register_index] == 1) ||
          (!quiesced_i && REG_LOCK[register_index] != 0));
      if (REG_LOCK[register_index] == 3 && REG_FSP[register_index] != int'(active_fsp_i)) locked = 1'b0;
      command_mask = request_i.data & byte_mask & REG_W1S[register_index];
      command_payload = (staging_q[channel_index][register_index] & REG_RW[register_index] & ~byte_mask) |
          (request_i.data & REG_RW[register_index] & byte_mask) | command_mask;
      for (int bit_index = 0; bit_index < 32; bit_index++) begin
        if (command_mask[bit_index]) needed += shared_access ? 1 : (broadcast ? CHANNELS : 1);
      end
      publication_request = command_mask != 0 && (register_index == INDEX_CONFIG_COMMIT || register_index == INDEX_DVFS);
      for (int c = 0; c < CHANNELS; c++)
        if (shared_access || c == channel_index || broadcast)
          candidate_image[c][register_index] = command_payload & REG_RW[register_index];
      if (publication_request) needed = 1;
      if (request_i.write) begin
        if (locked || state_q != IDLE ||
            (REG_RW[register_index] | REG_W1C[register_index] | REG_W1S[register_index]) == 0) bad_access = 1'b1;
        if (64'(sequence_q) + 64'(needed) >= 64'h100000000 || (!publication_request && count_q + needed > COMMAND_DEPTH)) bad_access = 1'b1;
        if (register_index == INDEX_DVFS && command_mask != 0 && command_payload[1:0] == 3)
          bad_access = 1'b1;
        if (register_index == INDEX_TRAIN_ACK && command_mask != 0 &&
            (!staging_q[0][INDEX_TRAIN_REQUEST][0] ||
             staging_q[0][INDEX_TRAIN_TOKEN] != current_boot)) bad_access = 1'b1;
        if (publication_request) begin
          if (!host_settled_i || (enabled_i && !quiesced_i) || sequence_q == 32'hffffffff ||
              fault_q || link_fault_i) bad_access = 1'b1;
          for (int c = 0; c < CHANNELS; c++)
            if (!image_valid(candidate_image[c])) snapshot_ok = 1'b0;
          if (!snapshot_ok) bad_access = 1'b1;
        end
      end
      response_o.data = staging_q[channel_index][register_index] &
          (REG_RW[register_index] | REG_RO[register_index] | REG_W1C[register_index]);
      if (register_index == INDEX_IRQ_STATUS) response_o.data = {19'b0, irq_q};
      if (register_index == INDEX_IRQ_MASK) response_o.data[31:13] = 0;
    end
    pready_o = preset_n && psel_i && penable_i && !access_q;
    response_o.error = bad_access;
    access_fire = pready_o;
    if (!psel_i || !penable_i) access_d = 1'b0;
    else if (pready_o) access_d = 1'b1;

    if (command_valid_o && command_ready_i) begin
      head_d = head_q == COMMAND_DEPTH - 1 ? 0 : head_q + 1;
      count_d--;
    end
    if (access_fire && request_i.write && !bad_access) begin
      for (int c = 0; c < CHANNELS; c++) begin
        if (shared_access || c == channel_index || broadcast) begin
          staging_d[c][register_index] = (staging_q[c][register_index] &
              ~(REG_RW[register_index] & byte_mask)) | (request_i.data & REG_RW[register_index] & byte_mask);
          staging_d[c][register_index] &= ~(request_i.data & REG_W1C[register_index] & byte_mask);
        end
      end
      if (register_index == INDEX_IRQ_MASK)
        for (int c = 0; c < CHANNELS; c++) staging_d[c][INDEX_IRQ_MASK][31:13] = 0;
      if (register_index == INDEX_IRQ_STATUS) clear_events = request_i.data[12:0] & byte_mask[12:0];
      if (publication_request) begin
        // GO and CONFIG_COMMIT share one atomic publication owner. The
        // byte-strobed FSP target is merged before validation and freezing;
        // no unhandled GO command can escape to a different coordinator.
        frozen_d = candidate_image;
        boot_d = current_boot;
        sequence_d = sequence_q + 1;
        prior_valid_d = valid_q;
        declined_d = 0;
        valid_d = 1'b0;
        state_d = HASH_IMAGE;
        hash_index_d = 0;
        crc_d = '1;
        timer_d = 0;
        sent_d = 0;
        acked_d = 0;
      end else begin
        // Preflight above covers every command and destination before any
        // staging value, sequence or mailbox slot is mutated.
        for (int bit_index = 0; bit_index < 32; bit_index++) begin
          if (command_mask[bit_index]) begin
            for (int c = 0; c < CHANNELS; c++) begin
              if ((shared_access && c == 0) || (!shared_access && (broadcast || c == channel_index))) begin
                sequence_d++;
                commands_d[tail_d] = '{sequence_number: sequence_d, boot_epoch: current_boot,
                    destinations: shared_access ? 2'b00 : (2'b01 << c),
                    register_index: 10'(register_index), command_mask: (32'd1 << bit_index),
                    write_data: command_payload, training_token: staging_q[0][INDEX_TRAIN_TOKEN]};
                tail_d = tail_d == COMMAND_DEPTH - 1 ? 0 : tail_d + 1;
                count_d++;
              end
            end
          end
        end
      end
    end

    if (global_status_valid_i) begin
      hardware_state_d = global_status_i[INDEX_STATUS] & REG_RO[INDEX_STATUS];
      current_boot = global_status_i[INDEX_BOOT_EPOCH];
      for (int c = 0; c < CHANNELS; c++) begin
        for (int r = 0; r < REG_COUNT; r++) begin
          // Invalidate live channel observations with the boot, without
          // erasing retained diagnostics or explicitly captured performance.
          if (current_boot != staging_q[0][INDEX_BOOT_EPOCH] && !REG_SHARED[r] && REG_RO[r] != 0 &&
              !(r >= INDEX_ERROR_ADDR_LO && r <= INDEX_PERF_CYCLES_1))
            staging_d[c][r] = REG_RESET[r];
          if (REG_SHARED[r] && REG_RO[r] != 0 && r != INDEX_ID && r != INDEX_CONFIG && r != INDEX_CONFIG_EPOCH)
            staging_d[c][r] = global_status_i[r] & REG_RO[r];
        end
      end
    end
    for (int c = 0; c < CHANNELS; c++) begin
      if (|(clear_events & first_events_q[c])) first_error_d[c] = 1'b0;
      if (status_valid_i[c] && diagnostic_active_q) begin
        // A retained occurrence keeps the boot/configuration of the event.
        // Destructive recovery does not discard it. Only the live snapshot
        // portion is freshness-gated; acknowledging an old event must still
        // count/capture it without replacing the current hardware state.
        set_events |= status_i[c].events;
        for (int r = 0; r < REG_COUNT; r++) begin
          if (status_i[c].events == 0 && !status_i[c].error_valid &&
              status_i[c].image[INDEX_BOOT_EPOCH] == current_boot &&
              status_i[c].image[INDEX_CONFIG_EPOCH] == epoch_q &&
              !REG_SHARED[r] && REG_RO[r] != 0 &&
              !(r >= INDEX_ERROR_ADDR_LO && r <= INDEX_UE_COUNT) &&
              (!(r >= INDEX_PERF_PAYLOAD_RD_0 && r <= INDEX_PERF_CYCLES_1) || status_i[c].perf_capture))
            staging_d[c][r] = status_i[c].image[r] & REG_RO[r];
        end
        if (status_i[c].events[5] && staging_q[c][INDEX_CE_COUNT] != 32'hffffffff)
          staging_d[c][INDEX_CE_COUNT] = staging_q[c][INDEX_CE_COUNT] + 1;
        if (status_i[c].events[6] && staging_q[c][INDEX_UE_COUNT] != 32'hffffffff)
          staging_d[c][INDEX_UE_COUNT] = staging_q[c][INDEX_UE_COUNT] + 1;
        if (status_i[c].error_valid) begin
          if (!first_error_d[c]) begin
            for (int r = INDEX_ERROR_ADDR_LO; r <= INDEX_ERROR_INFO; r++)
              staging_d[c][r] = status_i[c].image[r] & REG_RO[r];
            // Canonical ERROR_INFO reserves bits 31:18 despite its generated
            // storage field spanning a full word.
            staging_d[c][INDEX_ERROR_INFO][31:18] = 0;
            first_error_d[c] = 1'b1;
            first_events_d[c] = status_i[c].events & 13'h06e0;
          end else set_events[11] = 1'b1;
        end
      end
    end

    if (state_q != IDLE) begin
      timer_d = timer_q + 1;
      if (state_q == HASH_IMAGE) begin
        if (|config_ack_valid_i) bad_ack = 1'b1;
        for (int c = 0; c < CHANNELS; c++) crc_d[c] = crc_word(crc_q[c], frozen_q[c][hash_index_q]);
        if (hash_index_q == REG_COUNT - 1) begin
          state_d = PREPARE;
          sent_d = 0;
          acked_d = 0;
        end else hash_index_d++;
      end else begin
        for (int c = 0; c < CHANNELS; c++) begin
          config_valid_o[c] = !sent_q[c];
          config_o[c].epoch = sequence_q;
          config_o[c].payload_crc = ~crc_q[c];
          config_o[c].prepare = state_q == PREPARE;
          config_o[c].commit = state_q == COMMIT;
          config_o[c].abort_request = state_q == ABORT;
          config_o[c].destinations = 2'((1 << CHANNELS) - 1);
          if (config_valid_o[c] && config_ready_i[c]) sent_d[c] = 1'b1;
          if (config_ack_valid_i[c]) begin
            if ((!sent_q[c] && !(config_valid_o[c] && config_ready_i[c])) || acked_q[c] ||
                config_ack_i[c].sequence_number != sequence_q ||
                config_ack_i[c].boot_epoch != boot_q ||
                !((state_q == PREPARE && config_ack_i[c].phase == LPDDR5_MC_CONFIG_DECLINE
                    && !config_ack_i[c].accepted) ||
                  (config_ack_i[c].phase == (state_q == PREPARE ? LPDDR5_MC_CONFIG_PREPARE :
                    state_q == COMMIT ? LPDDR5_MC_CONFIG_COMMIT : LPDDR5_MC_CONFIG_ABORT)
                    && config_ack_i[c].accepted))) bad_ack = 1'b1;
            else begin
              ack_seen[c] = 1'b1;
              if (state_q == PREPARE) declined_d[c] = config_ack_i[c].phase == LPDDR5_MC_CONFIG_DECLINE;
            end
          end
        end
        acked_d = ack_seen;
        if (&ack_seen) begin
          if (state_q == PREPARE) begin
            if (|declined_d) begin
              // Only a unanimous decline attests that every destination kept
              // the old operating point and discarded its prepared image.
              // A mixed outcome may have changed physical state: never resume
              // it as though rollback had been established.
              if ((&declined_d) && prior_valid_q && !fault_q) begin
                valid_d = 1'b1;
                state_d = IDLE;
              end else bad_ack = 1'b1;
            end else begin
              state_d = COMMIT;
              sent_d = 0;
              acked_d = 0;
            end
          end else if (state_q == COMMIT) begin
            active_d = frozen_q;
            epoch_d = sequence_q;
            valid_d = 1'b1;
            set_events[2] = 1'b1;
            state_d = IDLE;
          end else state_d = IDLE;
        end
      end
      if (current_boot != boot_q || link_fault_i) bad_ack = 1'b1;
      if (bad_ack || timer_q >= PUBLICATION_TIMEOUT - 1) begin
        valid_d = 1'b0;
        fault_d = 1'b1;
        set_events[12] = 1'b1;
        if (state_q == ABORT) state_d = IDLE;
        else begin
          state_d = ABORT;
          timer_d = 0;
          sent_d = 0;
          acked_d = 0;
        end
      end
    end else if (|config_ack_valid_i) begin
      valid_d = 1'b0;
      fault_d = 1'b1;
      set_events[12] = 1'b1;
    end
    if (link_fault_i) begin
      valid_d = 1'b0;
      fault_d = 1'b1;
      set_events[12] = 1'b1;
    end
    if (recovery_confirmed_i && host_settled_i && state_q == IDLE && !link_fault_i) begin
      valid_d = 1'b0;
      fault_d = 1'b0;
      count_d = 0;
      head_d = 0;
      tail_d = 0;
    end
    irq_d = (irq_q & ~clear_events) | set_events;
    for (int c = 0; c < CHANNELS; c++) begin
      if (epoch_d != epoch_q) begin
        for (int r = 0; r < REG_COUNT; r++)
          if (!REG_SHARED[r] && REG_RO[r] != 0 &&
              !(r >= INDEX_ERROR_ADDR_LO && r <= INDEX_PERF_CYCLES_1))
            staging_d[c][r] = REG_RESET[r];
      end
      staging_d[c][INDEX_CONFIG_EPOCH] = epoch_d;
      staging_d[c][INDEX_STATUS] = hardware_state_d;
      staging_d[c][INDEX_STATUS][0] = valid_d;
      staging_d[c][INDEX_STATUS][4] = hardware_state_d[4] | fault_d;
      staging_d[c][INDEX_IRQ_STATUS] = {19'b0, irq_d};
    end
  end

  always_ff @(posedge pclk or negedge preset_n) begin
    if (!preset_n) begin
      diagnostic_active_q <= 0;
      state_q <= IDLE;
      for (int c = 0; c < CHANNELS; c++) begin
        frozen_q[c] <= RESET_IMAGE;
        active_q[c] <= RESET_IMAGE;
      end
      commands_q <= '0;
      head_q <= 0;
      tail_q <= 0;
      count_q <= 0;
      timer_q <= 0;
      hash_index_q <= 0;
      crc_q <= '0;
      sent_q <= '0;
      acked_q <= '0;
      declined_q <= '0;
      prior_valid_q <= 0;
      sequence_q <= 0;
      epoch_q <= 0;
      boot_q <= 0;
      hardware_state_q <= 0;
      valid_q <= 0;
      fault_q <= 0;
      access_q <= 0;
      last_protection_o <= 0;
    end else begin
      diagnostic_active_q <= 1;
      if (pready_o) last_protection_o <= request_i.protection;
      state_q <= state_d;
      frozen_q <= frozen_d;
      active_q <= active_d;
      commands_q <= commands_d;
      head_q <= head_d;
      tail_q <= tail_d;
      count_q <= count_d;
      timer_q <= timer_d;
      hash_index_q <= hash_index_d;
      crc_q <= crc_d;
      sent_q <= sent_d;
      acked_q <= acked_d;
      declined_q <= declined_d;
      prior_valid_q <= prior_valid_d;
      sequence_q <= sequence_d;
      epoch_q <= epoch_d;
      boot_q <= boot_d;
      hardware_state_q <= hardware_state_d;
      valid_q <= valid_d;
      fault_q <= fault_d;
      access_q <= access_d;
    end
  end

  // Diagnostic capture, counts, pending IRQs and explicit performance captures
  // have cold-reset custody. A common reset cannot consume an event or W1C.
  always_ff @(posedge pclk or negedge cold_preset_n) begin
    if(!cold_preset_n) begin first_error_q<='0;first_events_q<='0;irq_q<=0;end
    else if(diagnostic_active_q) begin
      first_error_q<=first_error_d;first_events_q<=first_events_d;irq_q<=irq_d;
    end
  end
  for(genvar c=0;c<CHANNELS;c++) begin : staging_channels
    for(genvar r=0;r<REG_COUNT;r++) begin : staging_registers
      logic [31:0] value_q;
      assign staging_q[c][r]=value_q;
      if(r>=INDEX_ERROR_ADDR_LO && r<=INDEX_PERF_CYCLES_1) begin : retained
        always_ff @(posedge pclk or negedge cold_preset_n) begin
          if(!cold_preset_n) value_q<=RESET_IMAGE[r];
          else if(diagnostic_active_q) value_q<=staging_d[c][r];
        end
      end else begin : volatile_register
        always_ff @(posedge pclk or negedge preset_n) begin
          if(!preset_n) value_q<=RESET_IMAGE[r];
          else value_q<=staging_d[c][r];
        end
      end
    end
  end

  initial begin
    assert (CHANNELS inside {1, 2} && PORTS inside {1, 2} && RANKS inside {1, 2});
    assert (AXI_BITS inside {64, 128, 256} && ADDRESS_BITS >= 32 && ADDRESS_BITS <= 40);
    assert (QUEUE_DEPTH inside {16, 32, 64} && COMMAND_DEPTH >= 4 && PUBLICATION_TIMEOUT > REG_COUNT + 8);
  end
endmodule
