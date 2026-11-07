// Occurrence storage is independent of live status and destructive recovery.
// A transfer moves one immutable notification into the host's reserved mailbox.
module lpddr5_mc_notifications #(
  parameter int DEPTH=16,
  parameter int INPUTS=3
) (
  input logic clk_ck_i,rst_ni,destructive_reset_i,
  input logic [INPUTS-1:0] occurrence_valid_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_notification_t [INPUTS-1:0] occurrence_i,
  output logic notification_valid_o,
  input logic notification_ready_i,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_notification_t notification_o,
  output logic overflow_o,
  input logic first_error_clear_i,
  input logic [63:0] first_error_clear_version_i,
  output logic first_error_valid_o,
  output logic [63:0] first_error_version_o,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_notification_t first_error_o
);
  import lpddr5_mc_channel_pkg::*;
  lpddr5_mc_channel_notification_t entries_q[DEPTH],entries_d[DEPTH],overflow_record_q,overflow_record_d,first_error_d;
  logic overflow_pending_q,overflow_pending_d,overflow_d,first_valid_d;
  logic [63:0] first_version_d;
  int unsigned read_q,write_q,count_q,read_d,write_d,count_d;
  logic clear_matches,captured,overflow_captured;
  assign notification_valid_o=count_q!=0;
  assign notification_o=notification_valid_o ? entries_q[read_q] : lpddr5_mc_channel_notification_t'('0);
  assign clear_matches=first_error_clear_i && first_error_valid_o && first_error_clear_version_i==first_error_version_o;
  initial begin
    assert(DEPTH>=2 && INPUTS>=1) else $fatal(1,"notification capacity");
  end
  always_comb begin
    for(int s=0;s<DEPTH;s++) entries_d[s]=entries_q[s];
    read_d=read_q;write_d=write_q;count_d=count_q;captured=0;overflow_captured=0;
    overflow_d=destructive_reset_i ? 1'b0 : overflow_o;
    overflow_pending_d=overflow_pending_q;overflow_record_d=overflow_record_q;
    first_valid_d=clear_matches ? 1'b0 : first_error_valid_o;
    first_version_d=first_error_version_o;first_error_d=first_error_o;
    if(notification_valid_o && notification_ready_i) begin read_d=(read_q+1)%DEPTH;count_d--;end
    // Separate reserved overflow storage never replaces a stalled head.
    if(overflow_pending_q && count_d<DEPTH) begin
      entries_d[write_d]=overflow_record_q;write_d=(write_d+1)%DEPTH;count_d++;overflow_pending_d=0;
    end
    for(int p=0;p<INPUTS;p++) if(occurrence_valid_i[p]) begin
      if(occurrence_i[p].error_valid && !captured && (!first_error_valid_o || clear_matches)) begin
        captured=1;
        // Never reuse a diagnostic version after exhaustion.
        if(first_error_version_o!='1) begin
          first_valid_d=1;first_error_d=occurrence_i[p];first_version_d=first_error_version_o+1;
        end else overflow_d=1;
      end
      if(count_d<DEPTH) begin
        entries_d[write_d]=occurrence_i[p];write_d=(write_d+1)%DEPTH;count_d++;
      end else begin
        overflow_d=1;
        if(!overflow_o && !overflow_pending_q && !overflow_captured) begin
          overflow_captured=1;overflow_record_d='0;
          overflow_record_d.events=13'h0a00; // DFI fault and detail overflow.
          overflow_record_d.boot_epoch=occurrence_i[p].boot_epoch;
          overflow_record_d.config_epoch=occurrence_i[p].config_epoch;overflow_pending_d=1;
        end
      end
    end
  end
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if(!rst_ni) begin
      read_q<=0;write_q<=0;count_q<=0;overflow_o<=0;overflow_pending_q<=0;overflow_record_q<='0;
      first_error_valid_o<=0;first_error_version_o<=0;first_error_o<='0;
      for(int s=0;s<DEPTH;s++) entries_q[s]<='0;
    end else begin
      read_q<=read_d;write_q<=write_d;count_q<=count_d;overflow_o<=overflow_d;
      overflow_pending_q<=overflow_pending_d;overflow_record_q<=overflow_record_d;
      first_error_valid_o<=first_valid_d;first_error_version_o<=first_version_d;first_error_o<=first_error_d;
      for(int s=0;s<DEPTH;s++) entries_q[s]<=entries_d[s];
    end
  end
endmodule
