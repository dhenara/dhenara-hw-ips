// Draft internal contracts. No controller implementation or interface freeze.
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
  // The actual external DFI option set still requires qualification.
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
endpackage
