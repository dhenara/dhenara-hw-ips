// CK-domain atomic snapshot consumer. The external all-channel coordinator
// owns commit publication; this consumer never commits from prepare alone.
module lpddr5_mc_channel_config #(
  parameter int CHANNEL_INDEX=0
) (
  input logic clk_ck_i, rst_ni,
  input logic destructive_reset_i,
  input logic discard_prepared_i,
  input logic [31:0] boot_epoch_i,
  input logic drained_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_config_t config_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_config_mailbox_t mailbox_i,
  output logic prepared_valid_o, prepare_error_o,
  output logic [31:0] prepared_epoch_o, committed_epoch_o,
  output logic active_valid_o, fault_o,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_config_t prepared_o,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_config_t active_o
);
  import lpddr5_mc_channel_pkg::*;
  lpddr5_mc_channel_config_t shadow_q;
  logic [31:0] retired_epoch_q;
  logic retired_valid_q;
  logic destination, valid_snapshot;
  assign destination=mailbox_i.destinations[CHANNEL_INDEX];
  assign prepared_o=shadow_q;
  assign valid_snapshot=config_valid(config_i) && config_i.boot_epoch==boot_epoch_i
    && config_i.config_epoch==mailbox_i.epoch && config_crc(config_i)==mailbox_i.payload_crc
    && (!retired_valid_q || config_i.config_epoch>retired_epoch_q)
    && (!active_valid_o || config_i.config_epoch>committed_epoch_o);
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if (!rst_ni) begin
      prepared_valid_o<=0; prepare_error_o<=0; active_valid_o<=0; fault_o<=0;
      prepared_epoch_o<=0; committed_epoch_o<=0; retired_epoch_q<=0; retired_valid_q<=0;
      active_o<='0; shadow_q<='0;
    end else if (destructive_reset_i) begin
      prepared_valid_o<=0; prepare_error_o<=0; active_valid_o<=0; fault_o<=0;
      if (active_valid_o && (!retired_valid_q || committed_epoch_o>retired_epoch_q)) begin
        retired_epoch_q<=committed_epoch_o; retired_valid_q<=1;
      end
      if (prepared_valid_o && (!retired_valid_q || prepared_epoch_o>retired_epoch_q)
          && (!active_valid_o || prepared_epoch_o>committed_epoch_o)) begin
        retired_epoch_q<=prepared_epoch_o; retired_valid_q<=1;
      end
      active_o<='0; shadow_q<='0;
    end else begin
      prepare_error_o<=0;
      if (discard_prepared_i) begin
        // A normal decline consumes the attempted sequence even though the
        // old active image remains valid. Recovery must never lower this fence.
        if (prepared_valid_o && (!retired_valid_q || prepared_epoch_o>retired_epoch_q)) begin
          retired_epoch_q<=prepared_epoch_o; retired_valid_q<=1;
        end
        prepared_valid_o<=0; shadow_q<='0;
      end else if (destination && mailbox_i.abort_request) begin
        prepared_valid_o<=0; active_valid_o<=0; fault_o<=1;
      end else if (destination && mailbox_i.prepare) begin
        if (mailbox_i.commit || !drained_i || !valid_snapshot
            || (prepared_valid_o && (config_i!=shadow_q || prepared_epoch_o!=mailbox_i.epoch))) begin
          prepare_error_o<=1; fault_o<=1;
        end else begin shadow_q<=config_i; prepared_valid_o<=1; prepared_epoch_o<=mailbox_i.epoch; end
      end else if (destination && mailbox_i.commit) begin
        if (active_valid_o && committed_epoch_o==mailbox_i.epoch && active_o==config_i
            && config_crc(active_o)==mailbox_i.payload_crc) begin
          // Repeated publication of the same immutable image is idempotent.
        end else if (!drained_i || !prepared_valid_o || mailbox_i.epoch!=prepared_epoch_o
            || mailbox_i.payload_crc!=config_crc(shadow_q) || config_i!=shadow_q || fault_o) begin
          prepare_error_o<=1; fault_o<=1; active_valid_o<=0;
        end else begin
          active_o<=shadow_q; committed_epoch_o<=prepared_epoch_o;
          active_valid_o<=1; prepared_valid_o<=0;
        end
      end
    end
  end
endmodule
