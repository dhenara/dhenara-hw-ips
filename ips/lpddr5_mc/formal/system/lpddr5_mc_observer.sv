// Reusable simulation assertions. No formal proof is claimed by this source.
module lpddr5_mc_observer #(
  parameter int WIDTH = 1
) (
  input logic clk_i,
  input logic rst_ni,
  input logic valid_i,
  input logic ready_i,
  input logic [WIDTH-1:0] payload_i
);
  logic stalled_q;
  logic [WIDTH-1:0] held_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      stalled_q <= 1'b0;
      held_q <= '0;
    end else begin
      if (stalled_q) begin
        assert (valid_i && payload_i == held_q)
          else $fatal(1, "ready/valid payload changed while stalled");
      end
      stalled_q <= valid_i && !ready_i;
      held_q <= payload_i;
    end
  end
endmodule
