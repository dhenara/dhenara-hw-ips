// Canonical consumer contracts; technology-independent controller RTL is separate.
package lpddr5_mc_contract_pkg;
  typedef logic [63:0] lpddr5_mc_tag_t;
  typedef logic [39:0] lpddr5_mc_address_t;
  typedef logic [15:0] lpddr5_mc_axi_id_t;
  typedef logic [15:0] lpddr5_mc_age_t;

  typedef enum logic [2:0] {
    LPDDR5_MC_READ,
    LPDDR5_MC_WRITE,
    LPDDR5_MC_ACT_PAIR,
    LPDDR5_MC_PRECHARGE,
    LPDDR5_MC_REFRESH,
    LPDDR5_MC_MAINTENANCE
  } lpddr5_mc_operation_t;

  typedef enum logic [2:0] {
    LPDDR5_MC_OWN_RESET,
    LPDDR5_MC_OWN_PHY,
    LPDDR5_MC_OWN_CONTROLLER,
    LPDDR5_MC_OWN_QUIESCING,
    LPDDR5_MC_OWN_FAULT
  } lpddr5_mc_ownership_t;

  // Max-width payloads; wrappers zero unused high bits. Valid/ready sit
  // outside these payloads and use ordinary AXI/APB handshake semantics.
  typedef struct packed {
    lpddr5_mc_address_t address;
    lpddr5_mc_axi_id_t id;
    logic [7:0] len;
    logic [2:0] size;
    logic [1:0] burst;
    logic lock;
    logic [3:0] qos;
    logic [3:0] cache;
    logic [2:0] prot;
  } lpddr5_mc_axi_address_t;

  typedef struct packed {
    logic [255:0] data;
    logic [31:0] strobe;
    logic last;
  } lpddr5_mc_axi_write_t;

  typedef struct packed {
    logic [255:0] data;
    lpddr5_mc_axi_id_t id;
    logic [1:0] response;
    logic last;
  } lpddr5_mc_axi_read_t;

  typedef struct packed {
    lpddr5_mc_axi_id_t id;
    logic [1:0] response;
  } lpddr5_mc_axi_b_t;

  typedef struct packed {
    logic [17:0] address;
    logic write;
    logic [31:0] data;
    logic [3:0] strobe;
    logic [2:0] protection;
  } lpddr5_mc_apb_request_t;

  typedef struct packed {
    logic [31:0] data;
    logic error;
  } lpddr5_mc_apb_response_t;

  typedef struct packed {
    lpddr5_mc_tag_t tag;
    lpddr5_mc_axi_id_t axi_id;
    lpddr5_mc_address_t address;
    logic port_index;
    logic channel_index;
    logic rank_index;
    logic [1:0] bank_group;
    logic [1:0] bank;
    logic [17:0] row;
    logic [11:0] column;
    logic [1:0] qos_class;
    logic is_write;
    logic is_64_bytes;
    logic [15:0] generation;
    logic [511:0] data;
    logic [63:0] byte_enable;
  } lpddr5_mc_granule_t;

  // A pick is a revocable proposal. Issue must compare generation, row,
  // ownership, credits and live timing again before changing any state.
  typedef struct packed {
    logic [5:0] queue_index;
    logic [15:0] generation;
    lpddr5_mc_tag_t tag;
    lpddr5_mc_operation_t operation;
  } lpddr5_mc_pick_t;

  // One DFI cycle is one CK. The two CA edges share this 14-bit word;
  // this must never be expanded into slower-core command phases.
  typedef struct packed {
    logic [13:0] address;
    logic [1:0] cs;
  } lpddr5_mc_ca_t;

  // At WCK:CK=4, each of four words has two x16 transfers. A byte slice
  // therefore returns 16 bits per word and has its own valid indication.
  // Qualified generic fixtures use the raw, pin-order word/slice contract.
  typedef struct packed {
    logic [3:0][31:0] data;
    logic [3:0][1:0] valid;
  } lpddr5_mc_read_bus4_t;

  typedef struct packed {
    lpddr5_mc_address_t address;
    lpddr5_mc_axi_id_t axi_id;
    lpddr5_mc_tag_t tag;
    logic port_index;
    logic channel_index;
    logic [8:0] syndrome;
    logic correlated;
    logic uncorrectable;
    logic memory_trusted;
  } lpddr5_mc_error_t;

  typedef struct packed {
    logic [3:0][31:0] data;
    logic [3:0][3:0] mask;
    logic [3:0][1:0] enable;
    logic [3:0][1:0] cs;
    // Integration extension: two beats x two byte lanes per word.
    logic [3:0][3:0] link_parity;
  } lpddr5_mc_write_bus4_t;

  typedef struct packed {
    logic [3:0][31:0] data;
    logic [3:0][1:0] valid;
    logic [3:0][3:0] dnv;
    logic [3:0][3:0] link_parity;
  } lpddr5_mc_return4_t;

  typedef struct packed {
    // phase p0..p3, byte slice 0..1, then rank or toggle encoding.
    // One WCK instance per byte slice. At ratio2 upper phases are zero.
    logic [3:0][1:0] enable;
    logic [3:0][1:0][1:0] cs;
    logic [3:0][1:0][1:0] toggle;
  } lpddr5_mc_wck4_t;

  typedef struct packed {
    logic [3:0][1:0] enable;
    logic [3:0][1:0][1:0] cs;
  } lpddr5_mc_read_request4_t;

  typedef struct packed {
    lpddr5_mc_tag_t tag;
    logic [15:0] generation;
    logic [5:0] queue_index;
    logic accepted;
    logic issued;
    logic replay;
    logic row_opened;
    logic row_closed;
    logic rank_index;
    logic [3:0] bank;
    logic [17:0] row;
  } lpddr5_mc_event_t;

  typedef struct packed {
    lpddr5_mc_tag_t tag;
    logic [15:0] generation;
    logic [3:0] word_index;
    logic slice_index;
    logic split_half;
    logic [31:0] profile_epoch;
  } lpddr5_mc_word_identity_t;

  typedef struct packed {
    logic ctrlupd_req;
    logic phyupd_ack;
    logic init_start;
    logic phymstr_ack;
    logic lp_ctrl_req;
    logic lp_data_req;
    logic [1:0] frequency_ratio;
    logic [4:0] frequency;
    logic [1:0] fsp;
    logic [1:0] syscs_state;
    logic [1:0] refresh_request;
    logic [31:0] profile_epoch;
  } lpddr5_mc_management_out_t;

  typedef struct packed {
    logic ctrlupd_ack;
    logic phyupd_req;
    logic [1:0] phyupd_type;
    logic init_complete;
    logic [1:0] boot_state;
    logic phymstr_req;
    logic [1:0] phymstr_type;
    logic [1:0] phymstr_cs_state;
    logic phymstr_state_sel;
    logic lp_ctrl_ack;
    logic lp_data_ack;
    logic [31:0] training_epoch;
    logic error;
  } lpddr5_mc_management_in_t;

  typedef struct packed {
    logic [31:0] epoch;
    logic [31:0] payload_crc;
    logic prepare;
    logic commit;
    logic abort_request;
    logic [1:0] destinations;
  } lpddr5_mc_config_mailbox_t;

  typedef struct packed {
    logic [2:0] ports;
    logic [2:0] channels;
    logic [2:0] ranks;
    logic [8:0] axi_bits;
    logic [4:0] id_bits;
    logic [5:0] address_bits;
    logic [6:0] queue_depth;
    logic [2:0] service_div;
    logic [2:0] wck_ratio;
    logic read_link;
    logic write_link;
    logic raw_parity;
  } lpddr5_mc_parameters_t;

  function automatic logic [1:0] lpddr5_mc_dfi_ratio(input logic [2:0] wck_ratio);
    return wck_ratio == 2 ? 2'b01 : wck_ratio == 4 ? 2'b10 : 2'b11;
  endfunction

  function automatic logic lpddr5_mc_parameters_valid(input lpddr5_mc_parameters_t p);
    return p.ports inside {1,2} && p.channels inside {1,2} && p.ranks inside {1,2}
      && p.axi_bits inside {64,128,256} && p.id_bits >= 4 && p.id_bits <= 16
      && p.address_bits >= 32 && p.address_bits <= 40 && p.queue_depth inside {16,32,64}
      && p.service_div inside {1,2,4} && p.wck_ratio inside {2,4}
      && (!(p.read_link || p.write_link) || (p.raw_parity && p.wck_ratio == 4));
  endfunction
endpackage
