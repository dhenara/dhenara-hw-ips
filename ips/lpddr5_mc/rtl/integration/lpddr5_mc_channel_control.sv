// CK-side publication owner. Validate the original APB image CRC, freeze its
// decoded profile/targets, and acknowledge prepare only after actual readiness.
module lpddr5_mc_channel_control #(
  parameter int CHANNEL_INDEX=0
) (
  input logic clk_i,rst_ni,
  input logic valid_i,
  output logic ready_o,
  input lpddr5_mc_apb_pkg::lpddr5_mc_configuration_payload_t payload_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_profile_t [2:0] qualified_profiles_i,
  output logic ack_valid_o,
  input logic ack_ready_i,
  output lpddr5_mc_apb_pkg::lpddr5_mc_publication_ack_t ack_o,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_config_t config_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_config_mailbox_t mailbox_o,
  input logic prepared_valid_i,prepare_error_i,fsp_ready_i,fsp_declined_i,
  input logic [31:0] prepared_epoch_i,committed_epoch_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_status_t status_i,
  output logic fsp_command_valid_o,
  input logic command_ready_i,command_rejected_i,
  output logic [1:0] target_fsp_o,
  output logic [4:0] target_frequency_o,
  output logic fault_o
);
  import lpddr5_mc_apb_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  import lpddr5_mc_integration_pkg::*;
  typedef enum logic [3:0] {IDLE,PREPARE,WAIT_PREPARE,FSP_COMMAND,WAIT_FSP,WAIT_DECLINE,COMMIT,WAIT_COMMIT,ABORT,ACK} state_t;
  state_t state_q;
  lpddr5_mc_channel_config_t decoded;
  lpddr5_mc_profile_t active_profile_q;
  logic [31:0] observed_boot_q;
  logic [1:0] active_fsp_q;
  logic unused_status;
  assign unused_status=^status_i;
  logic decoded_valid,declined_q;
  logic [1:0] decoded_fsp;
  logic [4:0] decoded_frequency;
  logic [31:0] image_crc_q;
  logic active_valid_q;
  lpddr5_mc_channel_register_decode decoder (
    .image_i(payload_i.image),.qualified_profiles_i,.boot_epoch_i(payload_i.boot_epoch),
    .config_epoch_i(payload_i.publication.epoch),.config_o(decoded),.valid_o(decoded_valid),
    .target_fsp_o(decoded_fsp),.target_frequency_o(decoded_frequency));
  assign ready_o=state_q==IDLE;
  assign ack_valid_o=state_q==ACK;
  assign fsp_command_valid_o=state_q==FSP_COMMAND;
  always_comb begin
    mailbox_o='0;mailbox_o.epoch=config_o.config_epoch;mailbox_o.payload_crc=config_crc(config_o);
    mailbox_o.destinations=2'b01<<CHANNEL_INDEX;
    mailbox_o.prepare=state_q==PREPARE;
    mailbox_o.commit=state_q==COMMIT;
    mailbox_o.abort_request=state_q==ABORT;
  end
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if(!rst_ni) begin
      state_q<=IDLE;config_o<='0;active_profile_q<='0;observed_boot_q<=0;active_fsp_q<=0;active_valid_q<=0;ack_o<='0;
      target_fsp_o<=0;target_frequency_o<=0;image_crc_q<=0;fault_o<=0;declined_q<=0;
    end else begin
      if(status_i.boot_epoch!=observed_boot_q) begin
        observed_boot_q<=status_i.boot_epoch;active_valid_q<=0;fault_o<=0;
      end
      if(fsp_declined_i) declined_q<=1;
      case(state_q)
        IDLE: if(valid_i) begin
          ack_o.sequence_number<=payload_i.publication.epoch;ack_o.boot_epoch<=payload_i.boot_epoch;
          ack_o.phase<=payload_i.publication.prepare ? LPDDR5_MC_CONFIG_PREPARE :
            payload_i.publication.commit ? LPDDR5_MC_CONFIG_COMMIT : LPDDR5_MC_CONFIG_ABORT;
          ack_o.accepted<=0;
          if(payload_i.boot_epoch!=status_i.boot_epoch || !payload_i.publication.destinations[CHANNEL_INDEX]
              || $countones({payload_i.publication.prepare,payload_i.publication.commit,payload_i.publication.abort_request})!=1) begin
            fault_o<=1;state_q<=ACK;
          end else if(payload_i.publication.abort_request) begin
            state_q<=ABORT;active_valid_q<=0;
          end else if(payload_i.publication.prepare) begin
            if(!decoded_valid || image_crc(payload_i.image)!=payload_i.publication.payload_crc || !status_i.drained) begin
              fault_o<=1;state_q<=ACK;
            end else begin
              config_o<=decoded;target_fsp_o<=decoded_fsp;target_frequency_o<=decoded_frequency;
              image_crc_q<=payload_i.publication.payload_crc;declined_q<=0;state_q<=PREPARE;
            end
          end else if(payload_i.publication.epoch!=config_o.config_epoch ||
              payload_i.publication.payload_crc!=image_crc_q || image_crc(payload_i.image)!=image_crc_q ||
              decoded!=config_o || !prepared_valid_i || prepared_epoch_i!=config_o.config_epoch) begin
            fault_o<=1;state_q<=ACK;
          end else state_q<=COMMIT;
        end
        PREPARE: state_q<=WAIT_PREPARE;
        WAIT_PREPARE: begin
          if(prepare_error_i || status_i.fault) begin fault_o<=1;state_q<=ACK;end
          else if(prepared_valid_i && prepared_epoch_i==config_o.config_epoch) begin
            if(!active_valid_q || config_o.profile==active_profile_q && target_fsp_o==active_fsp_q) begin ack_o.accepted<=1;state_q<=ACK;end
            else state_q<=FSP_COMMAND;
          end
        end
        FSP_COMMAND: if(command_ready_i) state_q<=WAIT_FSP;
        WAIT_FSP: begin
          if(fsp_declined_i || declined_q) state_q<=WAIT_DECLINE;
          else if(status_i.fault || command_rejected_i) begin fault_o<=1;state_q<=ACK;end
          else if(fsp_ready_i) begin ack_o.accepted<=1;state_q<=ACK;end
        end
        WAIT_DECLINE: if(!prepared_valid_i) begin
          ack_o.phase<=LPDDR5_MC_CONFIG_DECLINE;ack_o.accepted<=0;state_q<=ACK;
        end
        COMMIT: state_q<=WAIT_COMMIT;
        WAIT_COMMIT: begin
          if(prepare_error_i || status_i.fault) begin fault_o<=1;state_q<=ACK;end
          else if(status_i.config_valid && committed_epoch_i==config_o.config_epoch) begin
            active_profile_q<=config_o.profile;active_fsp_q<=target_fsp_o;active_valid_q<=1;ack_o.accepted<=1;state_q<=ACK;
          end
        end
        ABORT: begin ack_o.accepted<=1;state_q<=ACK;end
        ACK: if(ack_ready_i) state_q<=IDLE;
        default: begin fault_o<=1;state_q<=ACK;end
      endcase
    end
  end
endmodule
