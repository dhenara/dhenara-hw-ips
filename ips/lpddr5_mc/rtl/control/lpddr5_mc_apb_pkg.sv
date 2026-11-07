package lpddr5_mc_apb_pkg;
  import lpddr5_mc_apb_metadata_pkg::*;

  typedef enum logic [1:0] {
    LPDDR5_MC_CONFIG_PREPARE, LPDDR5_MC_CONFIG_COMMIT, LPDDR5_MC_CONFIG_ABORT,
    // A terminal normal FSP decline: old profile retained, prepared image
    // discarded. It is valid only in response to this exact PREPARE.
    LPDDR5_MC_CONFIG_DECLINE
  } lpddr5_mc_publication_phase_t;

  typedef struct packed {
    lpddr5_mc_contract_pkg::lpddr5_mc_config_mailbox_t publication;
    logic [31:0] boot_epoch;
    lpddr5_mc_register_image_t image;
  } lpddr5_mc_configuration_payload_t;

  typedef struct packed {
    logic [31:0] sequence_number;
    logic [31:0] boot_epoch;
    lpddr5_mc_publication_phase_t phase;
    logic accepted;
  } lpddr5_mc_publication_ack_t;

  typedef struct packed {
    logic [31:0] sequence_number;
    logic [31:0] boot_epoch;
    // Zero denotes the shared controller owner, otherwise a channel bitmask.
    logic [1:0] destinations;
    logic [9:0] register_index;
    logic [31:0] command_mask;
    logic [31:0] write_data;
    logic [31:0] training_token;
  } lpddr5_mc_control_command_t;

  typedef struct packed {
    lpddr5_mc_register_image_t image;
    // Exactly-once event/error notification, not a repeated sticky CK image.
    // Ordinary status snapshots use events=0 and error_valid=0. Each CE/UE
    // notification increments the APB-owned counter once; its immutable error
    // detail lets APB retain/rearm the first uncleared software-visible error.
    logic [12:0] events;
    logic error_valid;
    // Only a response to PERF_SNAPSHOT may replace the held counter image.
    logic perf_capture;
  } lpddr5_mc_channel_status_t;
endpackage
