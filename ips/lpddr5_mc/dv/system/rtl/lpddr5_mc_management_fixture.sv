// Primitive request/response pins for the sampled management adapter. Each
// sequencer is a fixture controller; it does not establish producer behavior.
module lpddr5_mc_management_fixture(input logic clk_i,rst_ni);
  timeunit 1ps;timeprecision 1ps;
  int edge_q;
  logic [4:0] request,response;
  int age_q[5];
  function automatic bit level(input int lane,input int age);
    bit active;
    active=age>=1 && age<3;
    return active ^ (lane==1 || lane==3);
  endfunction
  for(genvar lane=0;lane<5;lane++) begin:g
    assign request[lane]=edge_q>=2 && edge_q<7;
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if(!rst_ni) begin age_q[lane]<=-1;response[lane]<=lane==1 || lane==3;end
      else if(request[lane]) begin
        age_q[lane]<=age_q[lane]+1;
        response[lane]<=level(lane,age_q[lane]+2);
      end else response[lane]<=lane==1 || lane==3;
    end
  end
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if(!rst_ni) edge_q<=0;
    else begin
      edge_q<=edge_q+1;
      if(edge_q<36) for(int i=0;i<5;i++)
        $display("{\"kind\":\"management\",\"edge\":%0d,\"lane\":%0d,\"request\":%s,\"response\":%s}",edge_q,i,request[i] ? "true" : "false",response[i] ? "true" : "false");
    end
  end
endmodule
