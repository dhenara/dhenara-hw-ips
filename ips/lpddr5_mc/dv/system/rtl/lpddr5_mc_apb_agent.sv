module lpddr5_mc_apb_agent #(
  parameter int TIMEOUT = 256
) (
  input logic clk_i, rst_ni,
  output logic select_o, enable_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_apb_request_t request_o,
  input logic ready_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_apb_response_t response_i
);
  import lpddr5_mc_contract_pkg::*;
  initial begin select_o=0; enable_o=0; request_o='0; end
  task automatic transfer(input lpddr5_mc_apb_request_t payload, output lpddr5_mc_apb_response_t response);
    int waited;
    waited=0;
    @(negedge clk_i); request_o=payload; select_o=1; enable_o=0;
    @(negedge clk_i); enable_o=1;
    do begin
      @(posedge clk_i); waited++;
      assert(waited<TIMEOUT && rst_ni) else $fatal(1,"APB timeout/reset");
    end while(!ready_i);
    response=response_i;
    @(negedge clk_i); select_o=0; enable_o=0;
  endtask
endmodule
