`timescale 1ns/1ps
// One stable bundled payload, held from source acceptance through destination
// consumption and the synchronized acknowledgement. No bitwise data CDC.
module lpddr5_mc_mailbox #(
  parameter int unsigned WIDTH = 32
) (
  input  logic sclk_i,
  input  logic srst_ni,
  input  logic s_enable_i,
  input  logic s_rearm_i,
  output logic s_rearm_ready_o,
  input  logic svalid_i,
  output logic sready_o,
  input  logic [WIDTH-1:0] sdata_i,
  output logic s_fault_o,
  input  logic dclk_i,
  input  logic drst_ni,
  input  logic d_enable_i,
  input  logic d_rearm_i,
  output logic d_rearm_ready_o,
  output logic dvalid_o,
  input  logic dready_i,
  output logic [WIDTH-1:0] ddata_o,
  output logic d_fault_o
);
  logic s_reset_n, d_reset_n, s_run, d_run;
  logic request_q, acknowledge_q;
  logic [WIDTH-1:0] payload_q;
  logic pending_q, exposed_q;
  (* ASYNC_REG = "TRUE" *) logic [1:0] request_dq, acknowledge_sq;

  initial assert (WIDTH > 0) else $fatal(1, "mailbox WIDTH must be positive");
  lpddr5_mc_cdc_fence fence (
    .wclk_i(sclk_i), .wrst_ni(srst_ni), .w_rearm_i(s_rearm_i),
    .rclk_i(dclk_i), .rrst_ni(drst_ni), .r_rearm_i(d_rearm_i),
    .w_link_rst_no(s_reset_n), .r_link_rst_no(d_reset_n),
    .w_fault_o(s_fault_o), .r_fault_o(d_fault_o), .w_run_o(s_run), .r_run_o(d_run)
  );
  assign s_rearm_ready_o = s_reset_n;
  assign d_rearm_ready_o = d_reset_n;
  assign sready_o = s_run && s_enable_i && request_q == acknowledge_sq[1];
  assign dvalid_o = d_run && (d_enable_i || exposed_q) && pending_q;

  always_ff @(posedge sclk_i or negedge s_reset_n) begin
    if (!s_reset_n) begin
      request_q <= 1'b0;
      payload_q <= WIDTH'(0);
      acknowledge_sq <= '0;
    end else begin
      acknowledge_sq <= {acknowledge_sq[0], acknowledge_q};
      if (svalid_i && sready_o) begin
        payload_q <= sdata_i;
        request_q <= ~request_q;
      end
    end
  end
  always_ff @(posedge dclk_i or negedge d_reset_n) begin
    if (!d_reset_n) begin
      request_dq <= '0;
      acknowledge_q <= 1'b0;
      pending_q <= 1'b0;
      exposed_q <= 1'b0;
      ddata_o <= WIDTH'(0);
    end else begin
      request_dq <= {request_dq[0], request_q};
      if (dvalid_o && dready_i) exposed_q <= 1'b0;
      else if (dvalid_o) exposed_q <= 1'b1;
      if (!pending_q && request_dq[1] != acknowledge_q && d_run) begin
        ddata_o <= payload_q;
        pending_q <= 1'b1;
      end
      if (dvalid_o && dready_i) begin
        pending_q <= 1'b0;
        acknowledge_q <= request_dq[1];
      end
    end
  end
endmodule
