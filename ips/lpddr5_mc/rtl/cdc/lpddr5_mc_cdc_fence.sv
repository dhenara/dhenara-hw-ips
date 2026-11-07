`timescale 1ns/1ps
// A reset at either endpoint aborts the transport, not the owning transaction.
// Rearm is an explicit recovery permission from the owner after its accepted
// work has settled (or its host has been reset) and fresh epochs are installed.
// Holding rearm high would defeat that interlock; use a recovery pulse.
module lpddr5_mc_cdc_fence (
  input  logic wclk_i,
  input  logic wrst_ni,
  input  logic w_rearm_i,
  input  logic rclk_i,
  input  logic rrst_ni,
  input  logic r_rearm_i,
  output logic w_link_rst_no,
  output logic r_link_rst_no,
  output logic w_fault_o,
  output logic r_fault_o,
  output logic w_run_o,
  output logic r_run_o
);
  wire link_arst_n = wrst_ni & rrst_ni;
  (* ASYNC_REG = "TRUE" *) logic [1:0] r_fault_wq, w_fault_rq;

  lpddr5_mc_reset_sync w_reset (.clk_i(wclk_i), .arst_ni(link_arst_n), .rst_no(w_link_rst_no));
  lpddr5_mc_reset_sync r_reset (.clk_i(rclk_i), .arst_ni(link_arst_n), .rst_no(r_link_rst_no));

  // Async-set fault flops retain arbitrarily short reset assertions even if
  // the surviving endpoint clock is stopped. Synchronous release is explicit.
  always_ff @(posedge wclk_i or negedge w_link_rst_no) begin
    if (!w_link_rst_no) w_fault_o <= 1'b1;
    else if (w_rearm_i) w_fault_o <= 1'b0;
  end
  always_ff @(posedge rclk_i or negedge r_link_rst_no) begin
    if (!r_link_rst_no) r_fault_o <= 1'b1;
    else if (r_rearm_i) r_fault_o <= 1'b0;
  end
  always_ff @(posedge wclk_i or negedge w_link_rst_no) begin
    if (!w_link_rst_no) r_fault_wq <= 2'b11;
    else r_fault_wq <= {r_fault_wq[0], r_fault_o};
  end
  always_ff @(posedge rclk_i or negedge r_link_rst_no) begin
    if (!r_link_rst_no) w_fault_rq <= 2'b11;
    else w_fault_rq <= {w_fault_rq[0], w_fault_o};
  end
  assign w_run_o = w_link_rst_no && !w_fault_o && !r_fault_wq[1];
  assign r_run_o = r_link_rst_no && !r_fault_o && !w_fault_rq[1];
endmodule
