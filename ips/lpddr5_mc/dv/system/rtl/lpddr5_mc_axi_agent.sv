// Blocking testbench API. Drive on falling edges, sample on rising edges.
// A timeout is a test failure; reset must be coordinated by the caller.
module lpddr5_mc_axi_agent #(
  parameter int TIMEOUT = 256
) (
  input logic clk_i, rst_ni,
  output logic aw_valid_o,
  input logic aw_ready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_address_t aw_o,
  output logic w_valid_o,
  input logic w_ready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_write_t w_o,
  output logic ar_valid_o,
  input logic ar_ready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_address_t ar_o,
  input logic b_valid_i,
  output logic b_ready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_b_t b_i,
  input logic r_valid_i,
  output logic r_ready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_read_t r_i
);
  import lpddr5_mc_contract_pkg::*;
  initial begin
    aw_valid_o=0; w_valid_o=0; ar_valid_o=0; b_ready_o=0; r_ready_o=0;
    aw_o='0; w_o='0; ar_o='0;
  end
  task automatic address(input bit write_request, input lpddr5_mc_axi_address_t payload);
    int waited;
    waited=0;
    @(negedge clk_i);
    assert(rst_ni) else $fatal(1,"agent called during reset");
    if (write_request) begin aw_o=payload; aw_valid_o=1; end
    else begin ar_o=payload; ar_valid_o=1; end
    do begin
      @(posedge clk_i); waited++;
      assert(waited<TIMEOUT && rst_ni) else $fatal(1,"AXI address timeout/reset");
    end while (!(write_request ? aw_ready_i : ar_ready_i));
    @(negedge clk_i);
    if (write_request) aw_valid_o=0; else ar_valid_o=0;
  endtask
  task automatic write_beat(input lpddr5_mc_axi_write_t payload);
    int waited;
    waited=0;
    @(negedge clk_i); w_o=payload; w_valid_o=1;
    do begin
      @(posedge clk_i); waited++;
      assert(waited<TIMEOUT && rst_ni) else $fatal(1,"AXI W timeout/reset");
    end while (!w_ready_i);
    @(negedge clk_i); w_valid_o=0;
  endtask
  task automatic response_b(input int hold_cycles, output lpddr5_mc_axi_b_t payload);
    int waited;
    waited=0;
    while(!b_valid_i) begin
      @(posedge clk_i); waited++;
      assert(waited<TIMEOUT && rst_ni) else $fatal(1,"AXI B timeout/reset");
    end
    repeat(hold_cycles) @(posedge clk_i);
    @(negedge clk_i); b_ready_o=1;
    @(posedge clk_i); payload=b_i;
    assert(b_valid_i) else $fatal(1,"B withdrawn");
    @(negedge clk_i); b_ready_o=0;
  endtask
  task automatic response_r(input int hold_cycles, output lpddr5_mc_axi_read_t payload);
    int waited;
    waited=0;
    while(!r_valid_i) begin
      @(posedge clk_i); waited++;
      assert(waited<TIMEOUT && rst_ni) else $fatal(1,"AXI R timeout/reset");
    end
    repeat(hold_cycles) @(posedge clk_i);
    @(negedge clk_i); r_ready_o=1;
    @(posedge clk_i); payload=r_i;
    assert(r_valid_i) else $fatal(1,"R withdrawn");
    @(negedge clk_i); r_ready_o=0;
  endtask
endmodule
