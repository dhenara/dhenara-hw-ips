`timescale 1ns/1ps
// Asynchronous assertion; each consumer clock releases its own reset.
module lpddr5_mc_reset_sync (
  input  logic clk_i,
  input  logic arst_ni,
  output logic rst_no
);
  (* ASYNC_REG = "TRUE" *) logic [1:0] release_q;
  always_ff @(posedge clk_i or negedge arst_ni) begin
    if (!arst_ni) release_q <= 2'b00;
    else release_q <= {release_q[0], 1'b1};
  end
  assign rst_no = release_q[1];
endmodule
