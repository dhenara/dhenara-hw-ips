// Host-local adapters preserve the canonical payloads without changing them.
package lpddr5_mc_host_pkg;
  import lpddr5_mc_contract_pkg::*;

  typedef struct packed {
    logic enable;
    logic [39:0] base;
    logic [40:0] size;
    logic [39:0][5:0] permutation;
  } lpddr5_mc_host_region_t;

  typedef struct packed {
    logic [15:0][1:0] qos_map;
    logic granule64, inline_ecc, posted;
    logic [1:0][7:0] read_limit, write_limit;
    logic [31:0] boot_epoch, config_epoch;
    lpddr5_mc_host_region_t [7:0] regions;
  } lpddr5_mc_host_config_t;

  typedef struct packed {
    lpddr5_mc_tag_t tag;
    logic [15:0] generation;
    logic [31:0] boot_epoch, config_epoch;
  } lpddr5_mc_host_identity_t;

  // Host response subset. Project diagnostic-extended channel completions by
  // named fields; a packed cast would shift data and completion flags.
  typedef struct packed {
    lpddr5_mc_host_identity_t identity;
    logic physical_done, integrity_done, failed, is_write;
    logic [511:0] data;
    logic [1:0] response;
  } lpddr5_mc_host_completion_t;

  typedef struct packed {
    lpddr5_mc_host_identity_t identity;
    logic exposed, handshaken;
    logic [1:0] response;
  } lpddr5_mc_host_feedback_t;

  typedef struct packed {
    lpddr5_mc_axi_address_t axi;
    logic [5:0] slot;
    logic [15:0] generation;
    logic [31:0] sequence_number;
  } lpddr5_mc_host_address_t;

  typedef struct packed {
    lpddr5_mc_axi_write_t axi;
    logic [5:0] slot;
    logic [15:0] generation;
  } lpddr5_mc_host_write_t;

  typedef struct packed {
    logic [5:0] slot;
    logic [15:0] generation;
    logic write_response, exposed, handshaken;
    logic [1:0] response;
  } lpddr5_mc_host_response_event_t;

  typedef struct packed {
    logic valid;
    logic channel_index, rank_index;
    logic [3:0] bank;
    logic [17:0] row;
    logic [11:0] column;
    logic [39:0] physical_address;
  } lpddr5_mc_host_decode_t;

  function automatic logic [40:0] beat_address(
      // The canonical address payload also carries response/diagnostic fields.
      /* verilator lint_off UNUSEDSIGNAL */
      input lpddr5_mc_axi_address_t a, input logic [8:0] beat);
      /* verilator lint_on UNUSEDSIGNAL */
    logic [40:0] at, size_bytes, wrap_bytes, boundary;
    size_bytes = 41'd1 << a.size;
    wrap_bytes = size_bytes * (41'(a.len) + 1);
    boundary = {1'b0,a.address} & ~(wrap_bytes - 1);
    at = {1'b0,a.address};
    if (a.burst == 2'b01 && beat != 0)
      at = ({1'b0,a.address} & ~(size_bytes - 1)) + 41'(beat) * size_bytes;
    if (a.burst == 2'b10)
      at = boundary + (({1'b0,a.address} - boundary + 41'(beat)*size_bytes) & (wrap_bytes - 1));
    return at;
  endfunction

  function automatic logic [40:0] beat_limit(
      input lpddr5_mc_axi_address_t a, input logic [8:0] beat);
    logic [40:0] size_bytes;
    size_bytes = 41'd1 << a.size;
    return (beat_address(a,beat) & ~(size_bytes - 1)) + size_bytes;
  endfunction

  function automatic logic address_form_valid(
      input lpddr5_mc_axi_address_t a, input int data_bits, id_bits, address_bits);
    logic [40:0] last_address, limit;
    logic okay;
    okay = int'(a.size) <= $clog2(data_bits/8) && a.burst != 2'b11
      && (41'(a.address) >> address_bits) == 0 && (32'(a.id) >> id_bits) == 0;
    if (a.burst == 0) okay &= a.len < 16;
    if (a.burst == 2) okay &= (a.len inside {1,3,7,15})
      && (41'(a.address) & ((41'd1 << a.size)-1)) == 0;
    last_address = beat_address(a,{1'b0,a.len});
    limit = beat_limit(a,{1'b0,a.len});
    if (a.burst == 2) begin
      last_address = {1'b0,a.address} & ~(((41'(a.len)+1) << a.size)-1);
      limit = last_address + ((41'(a.len)+1) << a.size);
    end
    okay &= limit > 0 && (limit >> address_bits) <= 1 && limit <= (41'd1 << address_bits)
      && ((limit-1) >> 12) == (41'(a.address) >> 12)
      && (last_address >> 12) == (41'(a.address) >> 12);
    return okay;
  endfunction

  // Regions are sorted and packed by base, independent of their CSR indices.
  function automatic lpddr5_mc_host_decode_t decode_address(
      // Mapping consumes regions/ECC; the same immutable snapshot owns policy.
      /* verilator lint_off UNUSEDSIGNAL */
      input lpddr5_mc_host_config_t cfg, input logic [39:0] address,
      input int channels, ranks);
      /* verilator lint_on UNUSEDSIGNAL */
    lpddr5_mc_host_decode_t result;
    logic [40:0] prefix, mapped, local_address, capacity, relative;
    result = '0;
    prefix = 0; mapped = 0; relative = 0;
    capacity = cfg.inline_ecc ? (41'd1073741824 / 576)*512 : 41'd1073741824;
    for (int i=0; i<8; i++) begin
      if (cfg.regions[i].enable && 41'(address) >= 41'(cfg.regions[i].base)
          && 41'(address) < 41'(cfg.regions[i].base) + cfg.regions[i].size) begin
        prefix = 0;
        for (int j=0; j<8; j++)
          if (cfg.regions[j].enable && cfg.regions[j].base < cfg.regions[i].base)
            prefix += cfg.regions[j].size;
        relative = 41'(address) - 41'(cfg.regions[i].base);
        mapped = 0;
        for (int bit_index=0; bit_index<40; bit_index++)
          if ((41'd1 << bit_index) < cfg.regions[i].size)
            mapped[bit_index] = relative[cfg.regions[i].permutation[bit_index]];
        mapped += prefix;
        result.channel_index = channels == 2 ? mapped[6] : 1'b0;
        local_address = channels == 2 ? {1'b0,mapped[40:7],mapped[5:0]} : mapped;
        result.rank_index = local_address >= capacity;
        if (result.rank_index) local_address -= capacity;
        result.valid = local_address < capacity && (ranks == 2 || !result.rank_index);
        result.bank = local_address[14:11];
        result.row = {3'b0,local_address[29:15]};
        result.column = {6'b0,local_address[10:5]};
        result.physical_address = local_address[39:0];
      end
    end
    return result;
  endfunction

  function automatic logic configuration_valid(
      /* verilator lint_off UNUSEDSIGNAL */
      input lpddr5_mc_host_config_t cfg, input int channels, ranks, address_bits);
      /* verilator lint_on UNUSEDSIGNAL */
    logic okay, any_region;
    logic [40:0] total, capacity;
    logic [39:0] seen;
    okay = 1; any_region = 0; total = 0;
    capacity = (cfg.inline_ecc ? (41'd1073741824/576)*512 : 41'd1073741824)*41'(channels*ranks);
    for (int i=0; i<8; i++) if (cfg.regions[i].enable) begin
      any_region = 1;
      total += cfg.regions[i].size;
      okay &= cfg.regions[i].base[11:0] == 0 && cfg.regions[i].size >= 4096
        && (cfg.regions[i].size & (cfg.regions[i].size - 1)) == 0
        && 41'(cfg.regions[i].base) + cfg.regions[i].size <= (41'd1 << address_bits);
      seen = 0;
      for (int b=0; b<40; b++) if ((41'd1 << b) < cfg.regions[i].size) begin
        okay &= cfg.regions[i].permutation[b] < 40
          && (41'd1 << cfg.regions[i].permutation[b]) < cfg.regions[i].size;
        if (cfg.regions[i].permutation[b] < 40) begin
          okay &= !seen[cfg.regions[i].permutation[b]];
          seen[cfg.regions[i].permutation[b]] = 1;
        end
        if (b < 6) okay &= cfg.regions[i].permutation[b] == 6'(b);
      end
      for (int j=0; j<i; j++) if (cfg.regions[j].enable)
        okay &= 41'(cfg.regions[i].base) >= 41'(cfg.regions[j].base)+cfg.regions[j].size
          || 41'(cfg.regions[j].base) >= 41'(cfg.regions[i].base)+cfg.regions[i].size;
    end
    return okay && any_region && total <= capacity;
  endfunction
endpackage
