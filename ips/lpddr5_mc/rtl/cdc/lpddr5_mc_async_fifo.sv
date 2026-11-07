`timescale 1ns/1ps
// Dual-clock payload FIFO. Only Gray pointers/control pass synchronizers;
// memory bytes are sampled after the synchronized publication pointer arrives.
module lpddr5_mc_async_fifo #(
  parameter int unsigned WIDTH = 32,
  parameter int unsigned DEPTH = 16,
  localparam int unsigned ADDR_BITS = $clog2(DEPTH),
  localparam int unsigned PTR_BITS = ADDR_BITS + 1
) (
  input  logic wclk_i,
  input  logic wrst_ni,
  input  logic w_enable_i,
  input  logic w_rearm_i,
  output logic w_rearm_ready_o,
  input  logic wvalid_i,
  output logic wready_o,
  output logic w_pending_o,
  input  logic [WIDTH-1:0] wdata_i,
  output logic w_fault_o,
  input  logic rclk_i,
  input  logic rrst_ni,
  input  logic r_enable_i,
  input  logic r_rearm_i,
  output logic r_rearm_ready_o,
  output logic rvalid_o,
  input  logic rready_i,
  output logic [WIDTH-1:0] rdata_o,
  output logic r_fault_o
);
  localparam logic [PTR_BITS-1:0] FULL_MASK = PTR_BITS'((1 << ADDR_BITS) | (1 << (ADDR_BITS-1)));
  logic [WIDTH-1:0] memory [DEPTH];
  logic [PTR_BITS-1:0] wbin_q, wgray_q, rbin_q, rgray_q;
  logic [PTR_BITS-1:0] wbin_next, wgray_next, rbin_next, rgray_next;
  (* ASYNC_REG = "TRUE" *) logic [PTR_BITS-1:0] rgray_w1, rgray_w2, wgray_r1, wgray_r2;
  logic full_q, empty_q, exposed_q, w_reset_n, r_reset_n, w_run, r_run;

  initial begin
    assert (WIDTH > 0) else $fatal(1, "FIFO WIDTH must be positive");
    assert (DEPTH >= 2 && (DEPTH & (DEPTH-1)) == 0) else $fatal(1, "FIFO DEPTH must be a power of two >=2");
  end
  lpddr5_mc_cdc_fence fence (
    .wclk_i, .wrst_ni, .w_rearm_i, .rclk_i, .rrst_ni, .r_rearm_i,
    .w_link_rst_no(w_reset_n), .r_link_rst_no(r_reset_n),
    .w_fault_o, .r_fault_o, .w_run_o(w_run), .r_run_o(r_run)
  );
  // Export the owning fence's actual local release. A replica synchronizer
  // cannot establish that a one-cycle rearm will reach this transport.
  assign w_rearm_ready_o = w_reset_n;
  assign r_rearm_ready_o = r_reset_n;
  // Writer-local drain witness includes every accepted entry until actual
  // reader consumption crosses back. A reset explicitly aborts this transport.
  assign w_pending_o = wgray_q != rgray_w2;
  assign wready_o = w_run && w_enable_i && !full_q;
  // Disabling new exposure cannot retract an already exposed stalled head.
  assign rvalid_o = r_run && (r_enable_i || exposed_q) && !empty_q;
  assign rdata_o = memory[rbin_q[ADDR_BITS-1:0]];
  assign wbin_next = wbin_q + PTR_BITS'(wvalid_i && wready_o);
  assign wgray_next = (wbin_next >> 1) ^ wbin_next;
  assign rbin_next = rbin_q + PTR_BITS'(rvalid_o && rready_i);
  assign rgray_next = (rbin_next >> 1) ^ rbin_next;

  always_ff @(posedge wclk_i or negedge w_reset_n) begin
    if (!w_reset_n) begin
      wbin_q <= '0;
      wgray_q <= '0;
      full_q <= 1'b0;
      rgray_w1 <= '0;
      rgray_w2 <= '0;
    end else begin
      wbin_q <= wbin_next;
      wgray_q <= wgray_next;
      full_q <= wgray_next == (rgray_w2 ^ FULL_MASK);
      rgray_w1 <= rgray_q;
      rgray_w2 <= rgray_w1;
    end
  end
  always_ff @(posedge wclk_i) begin
    if (wvalid_i && wready_o) memory[wbin_q[ADDR_BITS-1:0]] <= wdata_i;
  end
  always_ff @(posedge rclk_i or negedge r_reset_n) begin
    if (!r_reset_n) begin
      rbin_q <= '0;
      rgray_q <= '0;
      empty_q <= 1'b1;
      exposed_q <= 1'b0;
      wgray_r1 <= '0;
      wgray_r2 <= '0;
    end else begin
      rbin_q <= rbin_next;
      rgray_q <= rgray_next;
      if (rvalid_o && rready_i) exposed_q <= 1'b0;
      else if (rvalid_o) exposed_q <= 1'b1;
      empty_q <= rgray_next == wgray_r2;
      wgray_r1 <= wgray_q;
      wgray_r2 <= wgray_r1;
    end
  end
endmodule
