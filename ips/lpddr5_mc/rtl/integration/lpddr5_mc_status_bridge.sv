// A registered source for the coherent status mailbox. Notifications have
// priority and retain their original epochs. Snapshots never replay events.
module lpddr5_mc_status_bridge (
  input logic clk_i,rst_ni,
  input lpddr5_mc_apb_metadata_pkg::lpddr5_mc_register_image_t snapshot_i,
  input logic perf_capture_i,
  output logic perf_ready_o,
  input logic notification_valid_i,
  output logic notification_ready_o,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_notification_t notification_i,
  output logic valid_o,
  input logic ready_i,
  output lpddr5_mc_apb_pkg::lpddr5_mc_channel_status_t status_o
);
  import lpddr5_mc_apb_metadata_pkg::*;
  import lpddr5_mc_integration_pkg::*;
  logic available,perf_pending_q;
  lpddr5_mc_register_image_t perf_image_q;
  assign perf_ready_o=!perf_pending_q;
  assign available=!valid_o || ready_i;
  assign notification_ready_o=available;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if(!rst_ni) begin
      perf_pending_q<=0;
      valid_o<=0;status_o.events<=0;status_o.error_valid<=0;status_o.perf_capture<=0;
      for(int r=0;r<REG_COUNT;r++) begin status_o.image[r]<=0;perf_image_q[r]<=0;end
    end
    else begin
      if(perf_capture_i && perf_ready_o) begin perf_image_q<=snapshot_i;perf_pending_q<=1;end
      if(available) begin
      valid_o<=1;status_o.events<=0;status_o.error_valid<=0;status_o.perf_capture<=0;
      if(notification_valid_i) begin
        status_o.image<=error_image(notification_i.error,notification_i.boot_epoch,notification_i.config_epoch);
        status_o.events<=notification_i.events;status_o.error_valid<=notification_i.error_valid;
      end else if(perf_pending_q) begin
        status_o.image<=perf_image_q;status_o.perf_capture<=1;perf_pending_q<=0;
      end else begin
        status_o.image<=snapshot_i;
      end
      end
    end
  end
endmodule
