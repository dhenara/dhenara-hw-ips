// Common-domain host engine. The asynchronous admission linearization policy
// belongs to its wrapper, which grants before source READY.
module lpddr5_mc_host_core #(
  parameter int PORTS = 2,
  parameter int CHANNELS = 2,
  parameter int RANKS = 1,
  parameter int DATA_BITS = 128,
  parameter int ID_BITS = 8,
  parameter int ADDRESS_BITS = 40,
  parameter int DEPTH = 64,
  // Early posted eligibility requires an endpoint with authoritative atomic
  // exposure/error arbitration in this domain. A queued asynchronous response
  // endpoint uses the conservative default: wait for the integrity result.
  parameter bit POSTED_EARLY = 1'b0,
  // The asynchronous assembly retains the authoritative accepted ledger at
  // each source. On abort it settles that ledger while flushing CDC, so these
  // common-domain copies must not wait for feedback in the flushed transport.
  parameter bit EXTERNAL_ABORT_LEDGER = 1'b0,
  localparam int SLOTS = PORTS*2*DEPTH,
  localparam int BUS_BYTES = DATA_BITS/8
) (
  input logic clk_i, rst_ni,
  input logic enable_i, fatal_i, recover_i,
  input logic cfg_valid_i,
  output logic cfg_ready_o,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_config_t cfg_i,
  output logic config_valid_o, fault_o, host_settled_o, late_error_o,
  output logic [31:0] stale_completions_o,
  input logic [PORTS-1:0] aw_valid_i, ar_valid_i, w_valid_i,
  output logic [PORTS-1:0] aw_ready_o, ar_ready_o, w_ready_o,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_address_t [PORTS-1:0] aw_i, ar_i,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_write_t [PORTS-1:0] w_i,
  output logic [PORTS-1:0] b_valid_o, r_valid_o,
  input logic [PORTS-1:0] b_ready_i, r_ready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_b_t [PORTS-1:0] b_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_read_t [PORTS-1:0] r_o,
  output logic [PORTS-1:0][5:0] b_slot_o, r_slot_o,
  output logic [PORTS-1:0][15:0] b_generation_o, r_generation_o,
  input logic [PORTS-1:0] response_event_valid_i,
  output logic [PORTS-1:0] response_event_ready_o,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_response_event_t [PORTS-1:0] response_event_i,
  output logic [PORTS-1:0][1:0][DEPTH-1:0] core_slot_available_o,
  output logic [PORTS-1:0] retire_valid_o,
  input logic [PORTS-1:0] retire_ready_i,
  output lpddr5_mc_host_pkg::lpddr5_mc_host_response_event_t [PORTS-1:0] retire_o,
  output logic [CHANNELS-1:0] request_valid_o,
  input logic [CHANNELS-1:0] request_ready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_granule_t [CHANNELS-1:0] request_o,
  output logic [CHANNELS-1:0][31:0] request_boot_epoch_o, request_config_epoch_o,
  input logic [CHANNELS-1:0] completion_valid_i,
  output logic [CHANNELS-1:0] completion_ready_o,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_completion_t [CHANNELS-1:0] completion_i,
  output logic [CHANNELS-1:0] feedback_valid_o,
  input logic [CHANNELS-1:0] feedback_ready_i,
  output lpddr5_mc_host_pkg::lpddr5_mc_host_feedback_t [CHANNELS-1:0] feedback_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_host_pkg::*;
  typedef struct packed {
    logic valid, write_access, fully_buffered, validated, execution_done, read_data_done, merged, merge_follower;
    logic pending, physical_done, chunk_integrity_done, integrity_done, response_sent, acknowledged;
    logic exposed, feedback_exposure, feedback_handshake;
    logic port_index, channel_index;
    logic [5:0] source_slot;
    logic [15:0] generation;
    logic [1:0] response, offered_response;
    lpddr5_mc_axi_address_t address;
    logic [63:0] order_number;
    logic [39:0] hazard_low;
    logic [40:0] hazard_high;
    logic [8:0] received, validation_beat, cursor, response_beat;
    logic [8:0] chunk_first, chunk_beats;
    logic [5:0] chunk_offset;
    logic chunk_fast;
    lpddr5_mc_host_identity_t identity;
  } transaction_t;
  transaction_t [SLOTS-1:0] transactions;
  // Response RAM is reserved with each address credit. Contents become valid
  // only through received/validation/cursor counts; clearing megabytes on reset
  // would prevent ordinary RAM inference and adds no correctness guarantee.
  logic [255:0] payload [SLOTS][256];
  logic [31:0] strobes [SLOTS][256];
  // A dependent owns a bit until that original write has its final integrity
  // result. Slots cannot be recycled ahead of this observation. Data overlays
  // are snapshots, so later writes/slot generations cannot change a read.
  logic [SLOTS-1:0][SLOTS-1:0] dependencies;
  logic [SLOTS-1:0][511:0] forward_data;
  logic [SLOTS-1:0][63:0] forward_mask;
  // Only overlapping traffic uses the sequential byte composer. Unrelated
  // granules retain the ordinary request path. Keep original slots reserved
  // during composition; completions still drain, but retirement waits until
  // the snapshot and its dependency set have been installed atomically.
  logic compose_active_q, compose_write_q;
  int compose_slot_q, compose_channel_q, compose_source_q;
  logic [8:0] compose_beat_q;
  logic [SLOTS-1:0] compose_remaining_q, compose_dependencies_q;
  lpddr5_mc_granule_t compose_granule_q;
  logic [63:0] compose_mask_q;
  int compose_next;
  lpddr5_mc_host_config_t config_q;
  logic [63:0] next_order_q, next_tag_q;
  logic [PORTS-1:0][31:0] expected_sequence_q;
  logic [PORTS-1:0][$clog2(SLOTS)-1:0] aw_slot, ar_slot, w_slot;
  logic [PORTS-1:0][31:0] b_select, r_select, validation_select;
  logic [PORTS-1:0][31:0] retire_select;
  logic [CHANNELS-1:0][31:0] request_select, feedback_select;
  lpddr5_mc_granule_t [CHANNELS-1:0] request_q;
  logic [CHANNELS-1:0][$clog2(SLOTS)-1:0] request_owner_q;
  logic [CHANNELS-1:0] request_held_q;
  logic [PORTS-1:0] b_held_q, r_held_q;
  lpddr5_mc_axi_b_t [PORTS-1:0] b_payload_q;
  lpddr5_mc_axi_read_t [PORTS-1:0] r_payload_q;
  logic [PORTS-1:0][5:0] b_slot_q, r_slot_q;
  logic [PORTS-1:0][15:0] b_generation_q, r_generation_q;
  logic [CHANNELS-1:0][31:0] completion_select;
  logic ever_configured_q, recovery_epoch_required_q;
  logic any_live;
  assign host_settled_o = !any_live && !(|request_held_q) && !(|b_held_q) && !(|r_held_q);
  assign cfg_ready_o = host_settled_o && !enable_i && !recover_i;

  function automatic int slot_index(input int port_index, input logic write_access, input logic [5:0] slot);
    return port_index*2*DEPTH + (write_access ? 0 : DEPTH) + int'(slot);
  endfunction

  function automatic logic beat_mapped(input lpddr5_mc_axi_address_t a, input logic [8:0] beat);
    logic okay;
    logic [40:0] at, limit;
    lpddr5_mc_host_decode_t decoded;
    logic unused_geometry;
    at = beat_address(a,beat); limit = beat_limit(a,beat);
    okay = at < limit && limit <= (41'd1 << ADDRESS_BITS);
    // At most one bus word participates in a legal beat, including unaligned
    // first beats. Mapping every addressed byte also rejects a region hole.
    for (int lane=0; lane<BUS_BYTES; lane++) begin
      if (at + 41'(lane) < limit) begin
        decoded = decode_address(config_q,40'(at+41'(lane)),CHANNELS,RANKS);
        unused_geometry = ^decoded[75:0];
        okay &= decoded.valid;
      end
    end
    return okay;
  endfunction

  function automatic logic [31:0] allowed_strobes(input lpddr5_mc_axi_address_t a, input logic [8:0] beat);
    logic [31:0] mask;
    logic [40:0] at, limit;
    mask = 0; at = beat_address(a,beat); limit = beat_limit(a,beat);
    for (int lane=0; lane<BUS_BYTES; lane++)
      if (at+41'(lane) < limit) mask[5'((at+41'(lane)) % 41'(BUS_BYTES))] = 1;
    return mask;
  endfunction

  function automatic logic blocked_by_older(input int slot);
    logic blocked;
    blocked = 0;
    for (int j=0; j<SLOTS; j++)
      if (transactions[j].valid && transactions[j].order_number < transactions[slot].order_number
          && !transactions[j].integrity_done
          && 41'(transactions[j].hazard_low) < transactions[slot].hazard_high
          && 41'(transactions[slot].hazard_low) < transactions[j].hazard_high) blocked = 1;
    return blocked;
  endfunction

  function automatic logic forwarding_allowed(input int slot);
    logic okay;
    okay = !transactions[slot].write_access;
    for (int j=0; j<SLOTS; j++)
      if (transactions[j].valid && transactions[j].order_number < transactions[slot].order_number
          && !transactions[j].integrity_done
          && 41'(transactions[j].hazard_low) < transactions[slot].hazard_high
          && 41'(transactions[slot].hazard_low) < transactions[j].hazard_high)
        okay &= transactions[j].write_access && transactions[j].fully_buffered
          && transactions[j].validated && transactions[j].response==0;
    return okay;
  endfunction

  function automatic logic merge_context(input int leader, candidate);
    logic [39:0] mask;
    mask = config_q.granule64 ? ~40'd63 : ~40'd31;
    // Config publication requires complete settlement. Equal logical granule
    // and immutable configuration therefore imply equal region/rank/ECC and
    // profile epochs. Do not combine exclusive or differently attributed/QoS
    // traffic. FIXED/WRAP and multi-granule transactions retain serial issue.
    return transactions[candidate].valid && transactions[candidate].write_access
      && transactions[candidate].fully_buffered && transactions[candidate].validated
      && transactions[candidate].response==0 && !transactions[candidate].pending
      && !transactions[candidate].execution_done && transactions[candidate].cursor==0
      && transactions[candidate].address.burst==1 && !transactions[candidate].address.lock
      && (transactions[candidate].hazard_low & mask)==(transactions[leader].hazard_low & mask)
      && transactions[candidate].hazard_high <= {1'b0,(transactions[leader].hazard_low & mask)}
        +(config_q.granule64 ? 41'd64 : 41'd32)
      && transactions[candidate].address.cache==transactions[leader].address.cache
      && transactions[candidate].address.prot==transactions[leader].address.prot
      && config_q.qos_map[transactions[candidate].address.qos]==config_q.qos_map[transactions[leader].address.qos];
  endfunction

  function automatic logic merge_allowed(input int leader, candidate);
    logic okay;
    logic [40:0] low_address, high_address;
    low_address = {1'b0,(transactions[leader].hazard_low & (config_q.granule64 ? ~40'd63 : ~40'd31))};
    high_address = low_address+(config_q.granule64 ? 41'd64 : 41'd32);
    okay = merge_context(leader,leader) && merge_context(leader,candidate)
      && transactions[candidate].order_number>transactions[leader].order_number;
    for (int j=0; j<SLOTS; j++) if (transactions[j].valid && j!=leader && j!=candidate
        && transactions[j].order_number<transactions[candidate].order_number) begin
      // A read between originals is always a barrier, including a forwarded
      // read whose data was sampled but whose integrity is still outstanding.
      if (transactions[j].order_number>transactions[leader].order_number
          && 41'(transactions[j].hazard_low)<high_address && low_address<transactions[j].hazard_high
          && (!transactions[j].write_access || !merge_context(leader,j))) okay=0;
      if (transactions[j].order_number<transactions[leader].order_number && !transactions[j].integrity_done
          && 41'(transactions[j].hazard_low)<transactions[candidate].hazard_high
          && 41'(transactions[candidate].hazard_low)<transactions[j].hazard_high) okay=0;
    end
    return okay;
  endfunction

  always_comb begin
    compose_next = -1;
    for (int i=0; i<SLOTS; i++) if (compose_remaining_q[i]
        && (compose_next<0 || transactions[i].order_number<transactions[compose_next].order_number)) compose_next=i;
    any_live = 0;
    for (int i=0; i<SLOTS; i++) any_live |= transactions[i].valid;
    aw_ready_o = 0; ar_ready_o = 0; w_ready_o = 0;
    b_valid_o = b_held_q; r_valid_o = r_held_q; response_event_ready_o = '1;
    core_slot_available_o = 0; retire_valid_o = 0;
    request_valid_o = request_held_q & {CHANNELS{!fault_o && !fatal_i}};
    completion_ready_o = '1;
    feedback_valid_o = 0;
    for (int p=0; p<PORTS; p++) begin
      int reads_live, writes_live;
      reads_live = 0; writes_live = 0;
      for (int i=0; i<SLOTS; i++) if (transactions[i].valid && transactions[i].port_index == 1'(p)) begin
        if (transactions[i].write_access) writes_live++;
        else reads_live++;
      end
      aw_slot[p] = $clog2(SLOTS)'(slot_index(p,1,aw_i[p].slot));
      ar_slot[p] = $clog2(SLOTS)'(slot_index(p,0,ar_i[p].slot));
      w_slot[p] = $clog2(SLOTS)'(slot_index(p,1,w_i[p].slot));
      b_select[p] = -1; r_select[p] = -1; validation_select[p] = -1;
      retire_select[p] = -1; retire_o[p] = '0;
      for (int d=0; d<DEPTH; d++) begin
        core_slot_available_o[p][0][d] = !transactions[slot_index(p,1,6'(d))].valid
          && expected_sequence_q[p] != 32'hffffffff
          && writes_live < int'(config_q.write_limit[p]) && enable_i && config_valid_o && !fault_o && !fatal_i;
        core_slot_available_o[p][1][d] = !transactions[slot_index(p,0,6'(d))].valid
          && expected_sequence_q[p] != 32'hffffffff
          && reads_live < int'(config_q.read_limit[p]) && enable_i && config_valid_o && !fault_o && !fatal_i;
      end
      b_o[p] = b_payload_q[p]; r_o[p] = r_payload_q[p];
      b_slot_o[p] = b_slot_q[p]; r_slot_o[p] = r_slot_q[p];
      b_generation_o[p] = b_generation_q[p]; r_generation_o[p] = r_generation_q[p];
      if (enable_i && config_valid_o && !fault_o && !fatal_i) begin
        if (aw_valid_i[p] && int'(aw_i[p].slot)<DEPTH
            && aw_i[p].sequence_number == expected_sequence_q[p]
            && core_slot_available_o[p][0][$clog2(DEPTH)'(aw_i[p].slot)]) aw_ready_o[p] = 1;
        else if (ar_valid_i[p] && int'(ar_i[p].slot)<DEPTH
            && ar_i[p].sequence_number == expected_sequence_q[p]
            && core_slot_available_o[p][1][$clog2(DEPTH)'(ar_i[p].slot)]) ar_ready_o[p] = 1;
      end
      if (int'(w_i[p].slot)<DEPTH && transactions[w_slot[p]].valid
          && transactions[w_slot[p]].generation == w_i[p].generation
          && !transactions[w_slot[p]].fully_buffered) w_ready_o[p] = 1;
      for (int i=0; i<SLOTS; i++) if (transactions[i].valid && int'(transactions[i].port_index)==p) begin
        if (transactions[i].acknowledged && transactions[i].integrity_done
            && !transactions[i].feedback_exposure && !transactions[i].feedback_handshake
            && ($signed(retire_select[p]) < 0 || transactions[i].order_number < transactions[retire_select[p]].order_number))
          retire_select[p] = i;
        if (transactions[i].write_access && !transactions[i].response_sent
            && ($signed(b_select[p]) < 0 || transactions[i].order_number < transactions[b_select[p]].order_number)) b_select[p] = i;
        if (!transactions[i].write_access && !transactions[i].response_sent
            && ($signed(r_select[p]) < 0 || transactions[i].order_number < transactions[r_select[p]].order_number)) r_select[p] = i;
        if (!transactions[i].write_access && !transactions[i].validated
            && ($signed(validation_select[p]) < 0 || transactions[i].order_number < transactions[validation_select[p]].order_number))
          validation_select[p] = i;
      end
      if ($signed(retire_select[p]) >= 0 && !compose_active_q) begin
        retire_valid_o[p] = 1;
        retire_o[p].slot = transactions[retire_select[p]].source_slot;
        retire_o[p].generation = transactions[retire_select[p]].generation;
        retire_o[p].write_response = transactions[retire_select[p]].write_access;
        retire_o[p].response = transactions[retire_select[p]].response;
      end
    end
    for (int c=0; c<CHANNELS; c++) begin
      int pending_granules;
      pending_granules = 0;
      for (int i=0; i<SLOTS; i++)
        if (transactions[i].valid && transactions[i].pending && !transactions[i].merge_follower
            && transactions[i].channel_index == 1'(c)) pending_granules++;
      request_select[c] = -1; feedback_select[c] = -1; completion_select[c] = -1;
      request_o[c] = request_q[c];
      request_boot_epoch_o[c] = config_q.boot_epoch;
      request_config_epoch_o[c] = config_q.config_epoch;
      feedback_o[c] = '0;
      for (int i=0; i<SLOTS; i++) begin
        lpddr5_mc_host_decode_t decoded;
        logic unused_geometry;
        decoded = decode_address(config_q,40'(beat_address(transactions[i].address,transactions[i].cursor)),CHANNELS,RANKS);
        unused_geometry = ^decoded[74:0];
        if (transactions[i].valid) begin
        if (transactions[i].pending && transactions[i].channel_index == 1'(c)
            && transactions[i].identity == completion_i[c].identity
            && transactions[i].write_access == completion_i[c].is_write) completion_select[c] = i;
        if (transactions[i].channel_index == 1'(c) && transactions[i].pending
            && (transactions[i].feedback_exposure || transactions[i].feedback_handshake)
            && $signed(feedback_select[c]) < 0) feedback_select[c] = i;
        if (!compose_active_q && !request_held_q[c] && pending_granules < DEPTH && !fault_o && !fatal_i && transactions[i].fully_buffered
            && transactions[i].validated && !transactions[i].pending && !transactions[i].execution_done
            && !transactions[i].read_data_done && transactions[i].response == 0
            && (!blocked_by_older(i) || forwarding_allowed(i))) begin
          if (decoded.valid && decoded.channel_index == 1'(c)
              && ($signed(request_select[c]) < 0 || transactions[i].order_number < transactions[request_select[c]].order_number))
            request_select[c] = i;
        end
        end
      end
      if ($signed(feedback_select[c]) >= 0) begin
        feedback_valid_o[c] = 1;
        feedback_o[c].identity = transactions[feedback_select[c]].identity;
        feedback_o[c].exposed = transactions[feedback_select[c]].feedback_exposure;
        feedback_o[c].handshaken = transactions[feedback_select[c]].feedback_handshake;
        feedback_o[c].response = transactions[feedback_select[c]].offered_response;
      end
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin : state
    if (!rst_ni) begin
      for (int i=0; i<SLOTS; i++) begin
        transactions[i] <= '0; dependencies[i] <= '0; forward_data[i] <= '0; forward_mask[i] <= '0;
      end
      compose_active_q<=0; compose_write_q<=0; compose_slot_q<=0; compose_channel_q<=0;
      compose_source_q<=-1; compose_beat_q<=0; compose_remaining_q<='0;
      compose_dependencies_q<='0; compose_granule_q<='0; compose_mask_q<='0;
      for (int p=0; p<PORTS; p++) expected_sequence_q[p] <= 0;
      for (int c=0; c<CHANNELS; c++) begin request_q[c] <= '0; request_owner_q[c] <= 0; end
      config_q <= '0; config_valid_o <= 0; fault_o <= 0; late_error_o <= 0;
      ever_configured_q <= 0; recovery_epoch_required_q <= 0;
      b_held_q <= 0; r_held_q <= 0;
      for (int p=0; p<PORTS; p++) begin
        b_payload_q[p] <= '0; r_payload_q[p] <= '0;
        b_slot_q[p] <= 0; r_slot_q[p] <= 0;
        b_generation_q[p] <= 0; r_generation_q[p] <= 0;
      end
      request_held_q <= 0; next_order_q <= 0; next_tag_q <= 0; stale_completions_o <= 0;
    end else begin
      if (cfg_valid_i && cfg_ready_o) begin
        if (configuration_valid(cfg_i,CHANNELS,RANKS,ADDRESS_BITS)
            && (!ever_configured_q || cfg_i.boot_epoch > config_q.boot_epoch
                || (!recovery_epoch_required_q && cfg_i.boot_epoch == config_q.boot_epoch
                    && cfg_i.config_epoch > config_q.config_epoch))) begin
          config_q <= cfg_i; config_valid_o <= 1; ever_configured_q <= 1; recovery_epoch_required_q <= 0;
          if (!ever_configured_q || cfg_i.boot_epoch > config_q.boot_epoch) begin
            expected_sequence_q <= '0;
            next_tag_q <= 0; next_order_q <= 0;
          end
        end else begin config_valid_o <= 0; fault_o <= 1; end
      end
      if (recover_i && host_settled_o && !enable_i && !fatal_i) begin
        fault_o <= 0; config_valid_o <= 0;
        // Counters/tags restart only with a new externally coordinated epoch.
        next_tag_q <= 0; next_order_q <= 0;
        recovery_epoch_required_q <= 1;
        for (int p=0; p<PORTS; p++) expected_sequence_q[p] <= 0;
      end
      if (fatal_i || (fault_o && !(recover_i && host_settled_o && !enable_i))) begin
        fault_o <= 1; config_valid_o <= 0; request_held_q <= 0;
        compose_active_q<=0;
        recovery_epoch_required_q <= 1;
      end
      // Never silently wrap an identity/order space. Fatal settlement remains
      // possible and recovery requires a fresh boot epoch at all endpoints.
      if (!(cfg_valid_i && cfg_ready_o) && !recover_i
          && (next_tag_q > 64'hffffffffffffffff-64'(CHANNELS)
              || next_order_q > 64'hffffffffffffffff-64'(PORTS))) begin
        fault_o <= 1; config_valid_o <= 0; recovery_epoch_required_q <= 1;
      end
      for (int i=0; i<SLOTS; i++) if (transactions[i].valid) begin
        for (int j=0; j<SLOTS; j++) if (dependencies[i][j] && transactions[j].integrity_done) begin
          dependencies[i][j]<=0;
          if (transactions[j].response!=0 && transactions[i].response==0) transactions[i].response<=2'b10;
        end
        if (transactions[i].read_data_done && dependencies[i]=='0) begin
          transactions[i].execution_done<=1; transactions[i].integrity_done<=1;
        end
        if (fatal_i || fault_o) begin
          dependencies[i]<='0;
          if (transactions[i].response == 0) transactions[i].response <= 2'b10;
          if (transactions[i].exposed && transactions[i].offered_response == 0) late_error_o <= 1;
          if (!transactions[i].write_access || transactions[i].fully_buffered) transactions[i].execution_done <= 1;
          transactions[i].integrity_done <= 1;
          transactions[i].pending <= 0;
        end
        if (transactions[i].fully_buffered && transactions[i].validated && transactions[i].response != 0
            && !transactions[i].pending && dependencies[i]=='0 && !compose_active_q) begin
          transactions[i].execution_done <= 1;
          transactions[i].integrity_done <= 1;
        end
      end
      begin : admission
        logic [63:0] allocated;
        allocated = 0;
        for (int p=0; p<PORTS; p++) begin
          if (aw_ready_o[p] || ar_ready_o[p]) begin
            lpddr5_mc_host_address_t accepted;
            logic unused_sequence;
            lpddr5_mc_axi_address_t a;
            logic [40:0] low_address, high_address;
            int slot;
            accepted = aw_ready_o[p] ? aw_i[p] : ar_i[p]; a = accepted.axi;
            unused_sequence = ^accepted.sequence_number;
            slot = aw_ready_o[p] ? int'(aw_slot[p]) : int'(ar_slot[p]);
            low_address = beat_address(a,0); high_address = beat_limit(a,{1'b0,a.len});
            if (a.burst == 2) begin
              low_address = 41'(a.address) & ~(((41'(a.len)+1)<<a.size)-1);
              high_address = low_address + ((41'(a.len)+1)<<a.size);
            end
            transactions[slot] <= '0;
            dependencies[slot]<='0; forward_mask[slot]<='0;
            transactions[slot].valid <= 1;
            transactions[slot].write_access <= aw_ready_o[p];
            transactions[slot].fully_buffered <= !aw_ready_o[p];
            transactions[slot].validated <= 0;
            transactions[slot].port_index <= 1'(p);
            transactions[slot].source_slot <= accepted.slot;
            transactions[slot].generation <= accepted.generation;
            transactions[slot].address <= a;
            transactions[slot].order_number <= next_order_q + allocated;
            transactions[slot].hazard_low <= low_address[39:0];
            transactions[slot].hazard_high <= high_address;
            transactions[slot].response <= address_form_valid(a,DATA_BITS,ID_BITS,ADDRESS_BITS) ? 2'b00 : 2'b11;
            expected_sequence_q[p] <= expected_sequence_q[p] + 1;
            allocated++;
          end
        end
        if (allocated != 0) next_order_q <= next_order_q + allocated;
        if (allocated != 0 && next_order_q > 64'hffffffffffffffff-allocated) fault_o <= 1;
      end
      for (int p=0; p<PORTS; p++) begin
        if (retire_valid_o[p] && retire_ready_i[p]) transactions[retire_select[p]].valid <= 0;
        if (w_valid_i[p] && w_ready_o[p]) begin
          int slot;
          logic [8:0] beat;
          slot = int'(w_slot[p]); beat = transactions[slot].received;
          transactions[slot].received <= beat+1;
          if (!beat_mapped(transactions[slot].address,beat))
            transactions[slot].response <= 2'b11;
          else if (transactions[slot].response == 0
              && ((w_i[p].axi.strobe & ~allowed_strobes(transactions[slot].address,beat)) != 0
                  || w_i[p].axi.last != (beat == {1'b0,transactions[slot].address.len})))
            transactions[slot].response <= 2'b10;
          if (beat == {1'b0,transactions[slot].address.len}) begin
            transactions[slot].fully_buffered <= 1;
            transactions[slot].validated <= 1;
          end
        end
        if ($signed(validation_select[p]) >= 0) begin
          int slot;
          slot = validation_select[p];
          if (!beat_mapped(transactions[slot].address,transactions[slot].validation_beat))
            transactions[slot].response <= 2'b11;
          if (transactions[slot].validation_beat == {1'b0,transactions[slot].address.len}) transactions[slot].validated <= 1;
          else transactions[slot].validation_beat <= transactions[slot].validation_beat+1;
        end
        // The registers also keep the internal transport VALID contract stable.
        // AXI exposure is reported by the endpoint, after the CDC transport.
        if (b_held_q[p] && b_ready_i[p]) b_held_q[p] <= 0;
        if ((!b_held_q[p] || b_ready_i[p]) && $signed(b_select[p]) >= 0 && transactions[b_select[p]].execution_done) begin
          b_held_q[p] <= 1;
          b_payload_q[p].id <= transactions[b_select[p]].address.id;
          b_payload_q[p].response <= transactions[b_select[p]].response;
          b_slot_q[p] <= transactions[b_select[p]].source_slot;
          b_generation_q[p] <= transactions[b_select[p]].generation;
          transactions[b_select[p]].response_sent <= 1;
        end
        if (r_held_q[p] && r_ready_i[p]) r_held_q[p] <= 0;
        if ((!r_held_q[p] || r_ready_i[p]) && $signed(r_select[p]) >= 0 && transactions[r_select[p]].execution_done) begin
          int slot;
          slot = r_select[p]; r_held_q[p] <= 1;
          r_payload_q[p].id <= transactions[slot].address.id;
          r_payload_q[p].response <= transactions[slot].response;
          r_payload_q[p].data <= transactions[slot].response == 0 ? payload[slot][transactions[slot].response_beat[7:0]] : '0;
          r_payload_q[p].last <= transactions[slot].response_beat == {1'b0,transactions[slot].address.len};
          r_slot_q[p] <= transactions[slot].source_slot;
          r_generation_q[p] <= transactions[slot].generation;
          if (transactions[slot].response_beat == {1'b0,transactions[slot].address.len}) transactions[slot].response_sent <= 1;
          else transactions[slot].response_beat <= transactions[slot].response_beat+1;
        end
        if (response_event_valid_i[p]) begin
          int slot;
          slot = slot_index(p,response_event_i[p].write_response,response_event_i[p].slot);
          if (int'(response_event_i[p].slot)<DEPTH && transactions[slot].valid
              && transactions[slot].generation == response_event_i[p].generation) begin
            // Exposure can reach this domain after a failure notification.
            // Preserve that order-independent late-error evidence as well.
            if (response_event_i[p].write_response && response_event_i[p].response == 0
                && (response_event_i[p].exposed || response_event_i[p].handshaken)
                && (transactions[slot].response != 0 || fault_o || fatal_i)) late_error_o <= 1;
            if (response_event_i[p].exposed) begin
              transactions[slot].exposed <= 1;
              transactions[slot].offered_response <= response_event_i[p].response;
              transactions[slot].feedback_exposure <= transactions[slot].pending;
            end
            if (response_event_i[p].handshaken) begin
              transactions[slot].acknowledged <= 1;
              transactions[slot].offered_response <= response_event_i[p].response;
              transactions[slot].feedback_handshake <= transactions[slot].pending;
            end
          end
        end
      end
      begin : requests
        logic [63:0] allocated;
        logic composer_allocated;
        allocated = 0; composer_allocated=0;
        for (int c=0; c<CHANNELS; c++) begin
          if (request_valid_o[c] && request_ready_i[c]) begin
            request_held_q[c] <= 0;
            transactions[request_owner_q[c]].pending <= 1;
          end
          if ($signed(request_select[c]) >= 0 && !composer_allocated) begin
            int slot, granule_bytes, chunk_beats;
            logic [40:0] at, limit;
            logic fast;
            lpddr5_mc_host_decode_t decoded;
            logic unused_decode;
            lpddr5_mc_granule_t granule;
            logic [SLOTS-1:0] compose_sources;
            logic needs_composition;
            slot = request_select[c]; granule_bytes = config_q.granule64 ? 64 : 32;
            at = beat_address(transactions[slot].address,transactions[slot].cursor);
            limit = beat_limit(transactions[slot].address,transactions[slot].cursor);
            fast = transactions[slot].address.burst == 1 && int'(transactions[slot].address.size) == $clog2(BUS_BYTES)
              && at % 41'(granule_bytes) == 0
              && (int'(transactions[slot].address.len)+1-int'(transactions[slot].cursor)) >= granule_bytes/BUS_BYTES;
            chunk_beats = fast ? granule_bytes/BUS_BYTES : 1;
            decoded = decode_address(config_q,at[39:0],CHANNELS,RANKS);
            unused_decode = ^{decoded.valid,decoded.channel_index,decoded.physical_address};
            granule = '0;
            granule.tag = next_tag_q+allocated;
            granule.generation = transactions[slot].generation;
            granule.axi_id = transactions[slot].address.id;
            granule.address = {at[39:6],6'b0};
            if (!config_q.granule64) granule.address[5] = at[5];
            granule.port_index = transactions[slot].port_index;
            granule.channel_index = 1'(c); granule.rank_index = decoded.rank_index;
            granule.bank_group = decoded.bank[3:2]; granule.bank = decoded.bank[1:0];
            granule.row = decoded.row;
            granule.column = decoded.column & (config_q.granule64 ? 12'hffe : 12'hfff);
            granule.qos_class = config_q.qos_map[transactions[slot].address.qos];
            granule.is_write = transactions[slot].write_access; granule.is_64_bytes = config_q.granule64;
            for (int b=0; b<8; b++) if (b<chunk_beats) begin
              for (int lane=0; lane<BUS_BYTES; lane++) begin
                int offset;
                offset = fast ? b*BUS_BYTES+lane : int'(at % 41'(granule_bytes))+lane;
                if (fast || at+41'(lane)<limit) begin
                  int source_lane;
                  source_lane = fast ? lane : int'((at+41'(lane)) % 41'(BUS_BYTES));
                  granule.data[offset*8 +: 8] = payload[slot][8'(transactions[slot].cursor+9'(b))][source_lane*8 +: 8];
                  granule.byte_enable[offset] = transactions[slot].write_access
                    ? strobes[slot][8'(transactions[slot].cursor+9'(b))][source_lane] : 1'b1;
                end
              end
            end
            compose_sources='0;
            for (int j=0; j<SLOTS; j++) begin
              if (!transactions[slot].write_access && transactions[j].valid && transactions[j].write_access
                  && !transactions[j].integrity_done && transactions[j].order_number<transactions[slot].order_number
                  && 41'(transactions[j].hazard_low)<transactions[slot].hazard_high
                  && 41'(transactions[slot].hazard_low)<transactions[j].hazard_high) compose_sources[j]=1;
              if (transactions[slot].write_access && merge_allowed(slot,j)) compose_sources[j]=1;
            end
            needs_composition=compose_sources!='0;
            forward_mask[slot]<='0;
            request_owner_q[c] <= $clog2(SLOTS)'(slot);
            if (needs_composition) begin
              composer_allocated=1; compose_active_q<=1; compose_slot_q<=slot; compose_channel_q<=c;
              compose_write_q<=transactions[slot].write_access; compose_source_q<=-1; compose_beat_q<=0;
              if (transactions[slot].write_access) begin
                compose_sources[slot]=1;
                granule.data='0; granule.byte_enable='0;
              end
              compose_remaining_q<=compose_sources;
              compose_dependencies_q<=transactions[slot].write_access ? compose_sources : '0;
              compose_granule_q<=granule; compose_mask_q<='0;
            end else begin request_q[c]<=granule; request_held_q[c]<=1; end
            transactions[slot].channel_index <= 1'(c);
            transactions[slot].identity.tag <= granule.tag;
            transactions[slot].identity.generation <= granule.generation;
            transactions[slot].identity.boot_epoch <= config_q.boot_epoch;
            transactions[slot].identity.config_epoch <= config_q.config_epoch;
            transactions[slot].chunk_first <= transactions[slot].cursor;
            transactions[slot].chunk_beats <= 9'(chunk_beats);
            transactions[slot].chunk_offset <= 6'(at % 41'(granule_bytes));
            transactions[slot].chunk_fast <= fast;
            transactions[slot].physical_done <= 0;
            transactions[slot].chunk_integrity_done <= 0;
            transactions[slot].integrity_done <= 0;
            // Mark pending while the request is held so another channel cannot
            // allocate the same transaction before the queue accepts it.
            transactions[slot].pending <= 1;
            allocated++;
          end
          if (feedback_valid_o[c] && feedback_ready_i[c]) begin
            transactions[feedback_select[c]].feedback_exposure <= 0;
            transactions[feedback_select[c]].feedback_handshake <= 0;
          end
        end
        if (allocated != 0) next_tag_q <= next_tag_q+allocated;
        if (allocated != 0 && next_tag_q > 64'hffffffffffffffff-allocated) fault_o <= 1;
      end
      if (compose_active_q && !fatal_i && !fault_o) begin : compose
        if (compose_source_q>=0) begin
          logic [40:0] at, limit;
          logic contributed;
          at=beat_address(transactions[compose_source_q].address,compose_beat_q);
          limit=beat_limit(transactions[compose_source_q].address,compose_beat_q);
          contributed=0;
          for (int lane=0; lane<BUS_BYTES; lane++) begin
            int source_lane, offset;
            source_lane=int'((at+41'(lane))%41'(BUS_BYTES));
            offset=int'(at+41'(lane)-41'(compose_granule_q.address));
            if (at+41'(lane)<limit && offset>=0 && offset<(config_q.granule64 ? 64 : 32)
                && strobes[compose_source_q][compose_beat_q[7:0]][source_lane]
                && (compose_write_q || compose_granule_q.byte_enable[offset])) begin
              compose_granule_q.data[offset*8+:8]<=payload[compose_source_q][compose_beat_q[7:0]][source_lane*8+:8];
              compose_mask_q[offset]<=1;
              if (compose_write_q) compose_granule_q.byte_enable[offset]<=1;
              contributed=1;
            end
          end
          if (contributed && !compose_write_q) compose_dependencies_q[compose_source_q]<=1;
          if (compose_beat_q==9'(transactions[compose_source_q].address.len)) compose_source_q<=-1;
          else compose_beat_q<=compose_beat_q+1;
        end else if (compose_next>=0) begin
          compose_source_q<=compose_next; compose_remaining_q[compose_next]<=0; compose_beat_q<=0;
        end else begin
          request_q[compose_channel_q]<=compose_granule_q;
          request_held_q[compose_channel_q]<=1;
          compose_active_q<=0;
          if (!compose_write_q) begin
            forward_data[compose_slot_q]<=compose_granule_q.data;
            forward_mask[compose_slot_q]<=compose_mask_q;
            for (int j=0; j<SLOTS; j++) if (compose_dependencies_q[j]) begin
              dependencies[compose_slot_q][j]<=!transactions[j].integrity_done;
              if (transactions[j].response!=0) transactions[compose_slot_q].response<=2'b10;
            end
          end else begin
            for (int j=0; j<SLOTS; j++) if (compose_dependencies_q[j]) begin
              transactions[j].pending<=1; transactions[j].merged<=1;
              transactions[j].merge_follower<=j!=compose_slot_q;
              transactions[j].channel_index<=1'(compose_channel_q);
              transactions[j].identity.tag<=compose_granule_q.tag;
              transactions[j].identity.generation<=compose_granule_q.generation;
              transactions[j].identity.boot_epoch<=config_q.boot_epoch;
              transactions[j].identity.config_epoch<=config_q.config_epoch;
              transactions[j].chunk_first<=0; transactions[j].chunk_beats<=9'(transactions[j].address.len)+1;
              transactions[j].physical_done<=0; transactions[j].chunk_integrity_done<=0;
            end
          end
        end
      end
      for (int c=0; c<CHANNELS; c++) if (completion_valid_i[c]) begin
        logic found;
        found = 0;
        for (int i=0; i<SLOTS; i++) begin
          if (transactions[i].valid && transactions[i].pending && transactions[i].channel_index == 1'(c)
              && transactions[i].identity == completion_i[c].identity
              && transactions[i].write_access == completion_i[c].is_write) begin
            found = 1;
            if (completion_i[c].physical_done) transactions[i].physical_done <= 1;
            if (completion_i[c].integrity_done) transactions[i].chunk_integrity_done <= 1;
            if (completion_i[c].failed || completion_i[c].response != 0) begin
              if (transactions[i].response == 0) transactions[i].response <= 2'b10;
              if (transactions[i].exposed && transactions[i].offered_response == 0) late_error_o <= 1;
              // A correlated granule error is a transaction outcome. It must
              // not invent ownership loss and prevent a protected full-word
              // rewrite. Transport/ownership faults arrive through fatal_i.
            end
            if ((completion_i[c].integrity_done || transactions[i].chunk_integrity_done)
                && (completion_i[c].physical_done || transactions[i].physical_done
                    || completion_i[c].failed || completion_i[c].response != 0)) begin
              logic last_chunk;
              last_chunk = transactions[i].chunk_first+transactions[i].chunk_beats
                == 9'(transactions[i].address.len)+1;
              transactions[i].pending <= 0;
              transactions[i].feedback_exposure <= 0;
              transactions[i].feedback_handshake <= 0;
              transactions[i].cursor <= transactions[i].chunk_first+transactions[i].chunk_beats;
              if (last_chunk || completion_i[c].failed || completion_i[c].response != 0) begin
                if (transactions[i].write_access) begin
                  transactions[i].execution_done <= 1;
                  transactions[i].integrity_done <= 1;
                end else begin
                  transactions[i].read_data_done<=1;
                  // Keep the ordinary read completion latency when there is
                  // no remaining integrity dependency. A dependency cleared
                  // on this edge is observed on the next edge, with its error.
                  if (dependencies[i]=='0) begin
                    transactions[i].execution_done<=1;
                    transactions[i].integrity_done<=1;
                  end
                end
              end
            end else if (POSTED_EARLY && config_q.posted && !transactions[i].merged
                && transactions[i].write_access && completion_i[c].physical_done
                && transactions[i].chunk_first+transactions[i].chunk_beats == 9'(transactions[i].address.len)+1)
              transactions[i].execution_done <= 1;
          end
        end
        if (!found && stale_completions_o != 32'hffffffff) stale_completions_o <= stale_completions_o+1;
      end
      if (EXTERNAL_ABORT_LEDGER && (fatal_i || fault_o)) begin
        for (int i=0; i<SLOTS; i++) begin transactions[i] <= '0; dependencies[i]<='0; end
        compose_active_q<=0;
        request_held_q <= 0; b_held_q <= 0; r_held_q <= 0;
      end
    end
  end
  // Explicit write ports keep RAM writes separate from the transaction-state
  // loops and work with the qualified simulator's synthesizable array subset.
  for (genvar p=0; p<PORTS; p++) begin : write_ram
    always_ff @(posedge clk_i) if (w_valid_i[p] && w_ready_o[p]) begin
      payload[w_slot[p]][transactions[w_slot[p]].received[7:0]] <= w_i[p].axi.data;
      strobes[w_slot[p]][transactions[w_slot[p]].received[7:0]] <= w_i[p].axi.strobe;
    end
  end
  for (genvar c=0; c<CHANNELS; c++) begin : read_ram
    for (genvar b=0; b<8; b++) begin : beat_ram
      logic [255:0] value;
      always_comb begin
        logic [40:0] at, limit;
        int offset;
        value = 0;
        at = 0; limit = 0; offset = 0;
        if ($signed(completion_select[c]) >= 0) begin
          at = beat_address(transactions[completion_select[c]].address,transactions[completion_select[c]].chunk_first+9'(b));
          limit = beat_limit(transactions[completion_select[c]].address,transactions[completion_select[c]].chunk_first+9'(b));
          for (int lane=0; lane<BUS_BYTES; lane++) if (at+41'(lane)<limit) begin
            offset = transactions[completion_select[c]].chunk_fast ? b*BUS_BYTES+lane
              : int'(transactions[completion_select[c]].chunk_offset)+lane;
            value[int'((at+41'(lane)) % 41'(BUS_BYTES))*8 +: 8] = forward_mask[completion_select[c]][offset]
              ? forward_data[completion_select[c]][offset*8+:8] : completion_i[c].data[offset*8 +: 8];
          end
        end
      end
      always_ff @(posedge clk_i) begin
        if (completion_valid_i[c] && $signed(completion_select[c]) >= 0
            && !transactions[completion_select[c]].write_access
            && b<int'(transactions[completion_select[c]].chunk_beats)
            && (completion_i[c].integrity_done || transactions[completion_select[c]].chunk_integrity_done)
            && (completion_i[c].physical_done || transactions[completion_select[c]].physical_done))
          payload[completion_select[c]][8'(transactions[completion_select[c]].chunk_first+9'(b))] <= value;
      end
    end
  end
  initial begin
    assert (PORTS inside {1,2} && CHANNELS inside {1,2} && RANKS inside {1,2});
    assert (DATA_BITS inside {64,128,256} && ID_BITS>=4 && ID_BITS<=16 && ADDRESS_BITS>=32 && ADDRESS_BITS<=40);
    assert (DEPTH inside {16,32,64});
  end
endmodule
