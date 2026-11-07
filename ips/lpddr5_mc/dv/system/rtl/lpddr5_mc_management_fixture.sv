// Primitive sampled management pins. Separate sequences preserve the FSP and
// low-power release-before-return contracts. These are fixture controllers.
module lpddr5_mc_management_fixture(input logic clk_i,rst_ni);
  timeunit 1ps;timeprecision 1ps;
  int edge_q;
  logic [6:0] request,response;
  function automatic bit initial_level(input int lane);
    return lane==1 || lane==3 || lane==5;
  endfunction
  function automatic int release_age(input int lane);
    if(lane==3) return 6;
    if(lane==2 || lane>=5) return 4;
    return 5;
  endfunction
  function automatic bit level(input int lane,input int age);
    int returned;
    if(lane>=5) return initial_level(lane);
    returned=lane==3 ? 11 : lane==2 ? 6 : 3;
    return (age>=1 && age<returned) ^ initial_level(lane);
  endfunction
  for(genvar lane=0;lane<7;lane++) begin:g
    assign request[lane]=edge_q>=2 && edge_q<2+release_age(lane);
    always_ff @(posedge clk_i or negedge rst_ni) begin
      if(!rst_ni) response[lane]<=initial_level(lane);
      else response[lane]<=level(lane,edge_q-1);
    end
  end
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if(!rst_ni) edge_q<=0;
    else begin
      edge_q<=edge_q+1;
      if(edge_q<36) for(int i=0;i<7;i++)
        $display("{\"kind\":\"management\",\"edge\":%0d,\"lane\":%0d,\"request\":%s,\"response\":%s,\"clock_done\":%s,\"drained\":true,\"wck_disabled\":true,\"traffic\":false,\"known\":true}",edge_q,i,request[i] ? "true" : "false",response[i] ? "true" : "false",i==3 && edge_q>=7 ? "true" : "false");
    end
  end
endmodule
