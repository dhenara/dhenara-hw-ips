// Environment fixture only. This deliberately small buffered endpoint is NOT
// the LPDDR5 controller. It exercises agents, clocks, sampling and sensitivity.
module lpddr5_mc_fixture_endpoint #(
  parameter int PORT = 0
) (
  input logic clk_i, rst_ni,
  input logic aw_valid_i, w_valid_i, ar_valid_i,
  output logic aw_ready_o, w_ready_o, ar_ready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_address_t aw_i, ar_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_write_t w_i,
  output logic b_valid_o, r_valid_o,
  input logic b_ready_i, r_ready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_b_t b_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_read_t r_o
);
  timeunit 1ps; timeprecision 1ps;
  import lpddr5_mc_contract_pkg::*;
  logic active_q, read_q;
  int windex_q, rindex_q;
  logic [127:0] buffered_q[4], memory_q[4];
  lpddr5_mc_axi_address_t saved_aw_q, saved_ar_q;
  bit bad_stability;
  initial bad_stability=$test$plusargs("bad_stability");
  assign aw_ready_o = !active_q && !b_valid_o;
  assign w_ready_o = active_q;
  assign ar_ready_o = !read_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if(!rst_ni) begin
      active_q<=0; read_q<=0; windex_q<=0; rindex_q<=0;
      b_valid_o<=0; r_valid_o<=0; b_o<='0; r_o<='0;
      saved_aw_q<='0; saved_ar_q<='0;
      for(int i=0;i<4;i++) begin buffered_q[i]<='0; memory_q[i]<='0; end
    end else begin
      if(aw_valid_i && aw_ready_o) begin
        assert(aw_i.len==3 && aw_i.size==4 && aw_i.burst==1) else $fatal(1,"fixture AW profile");
        saved_aw_q<=aw_i; active_q<=1; windex_q<=0;
        $display("{\"kind\":\"aw\",\"ps\":%0t,\"key\":%0d,\"port\":%0d,\"id\":%0d,\"address\":%0d,\"length\":3,\"size\":4,\"burst\":1,\"lock\":false}", $time,PORT*2,PORT,aw_i.id,aw_i.address);
      end
      if(w_valid_i && w_ready_o) begin
        assert(w_i.strobe==32'hffff && w_i.last==(windex_q==3)) else $fatal(1,"fixture W protocol");
        buffered_q[windex_q]<=w_i.data[127:0];
        $display("{\"kind\":\"w\",\"ps\":%0t,\"port\":%0d,\"data\":\"0x%032x\",\"strobe\":65535,\"last\":%s}", $time,PORT,w_i.data[127:0],w_i.last ? "true" : "false");
        if(windex_q==3) begin
          // Observe effects only after the complete buffered write validates.
          for(int i=0;i<4;i++) begin
            memory_q[i]<=i==3 ? w_i.data[127:0] : buffered_q[i];
            $write("{\"kind\":\"pin\",\"ps\":%0t,\"key\":%0d,\"address\":%0d,\"hex\":\"",$time,PORT*2,saved_aw_q.address+40'(i*16));
            for(int j=0;j<16;j++) begin
              if(i==3) $write("%02x",w_i.data[j*8+:8]); else $write("%02x",buffered_q[i][j*8+:8]);
            end
            $display("\",\"enables\":65535,\"epoch\":0}");
          end
          $display("{\"kind\":\"physical\",\"ps\":%0t,\"key\":%0d}",$time,PORT*2);
          $display("{\"kind\":\"integrity\",\"ps\":%0t,\"key\":%0d,\"failed\":false}",$time,PORT*2);
          b_valid_o<=1; b_o.id<=saved_aw_q.id; b_o.response<=0; active_q<=0;
        end else windex_q<=windex_q+1;
      end
      if(b_valid_o) begin
        $display("{\"kind\":\"b\",\"ps\":%0t,\"key\":%0d,\"response\":%0d,\"ready\":%s,\"posted\":false}",$time,PORT*2,b_o.response,b_ready_i ? "true" : "false");
        if(b_ready_i) b_valid_o<=0;
        else if(bad_stability) b_o.response<=2;
      end
      if(ar_valid_i && ar_ready_o) begin
        assert(ar_i.len==3 && ar_i.size==4 && ar_i.burst==1) else $fatal(1,"fixture AR profile");
        saved_ar_q<=ar_i; read_q<=1; rindex_q<=0; r_valid_o<=1;
        r_o.data<={128'b0,memory_q[0]}; r_o.id<=ar_i.id; r_o.response<=0; r_o.last<=0;
        $display("{\"kind\":\"ar\",\"ps\":%0t,\"key\":%0d,\"port\":%0d,\"id\":%0d,\"address\":%0d,\"length\":3,\"size\":4,\"burst\":1,\"lock\":false}", $time,PORT*2+1,PORT,ar_i.id,ar_i.address);
        $display("{\"kind\":\"physical\",\"ps\":%0t,\"key\":%0d}",$time,PORT*2+1);
        $display("{\"kind\":\"integrity\",\"ps\":%0t,\"key\":%0d,\"failed\":false}",$time,PORT*2+1);
      end
      if(r_valid_o) begin
        $display("{\"kind\":\"r\",\"ps\":%0t,\"key\":%0d,\"data\":\"0x%032x\",\"response\":%0d,\"last\":%s,\"ready\":%s}", $time,PORT*2+1,r_o.data[127:0],r_o.response,r_o.last ? "true" : "false",r_ready_i ? "true" : "false");
        if(r_ready_i) begin
          if(rindex_q==3) begin read_q<=0; r_valid_o<=0; end
          else begin
            rindex_q<=rindex_q+1; r_o.data<={128'b0,memory_q[rindex_q+1]};
            r_o.last<=rindex_q==2;
          end
        end
      end
    end
  end
  lpddr5_mc_observer #(.WIDTH($bits(lpddr5_mc_axi_b_t))) b_check(clk_i,rst_ni,b_valid_o,b_ready_i,b_o);
  lpddr5_mc_observer #(.WIDTH($bits(lpddr5_mc_axi_read_t))) r_check(clk_i,rst_ni,r_valid_o,r_ready_i,r_o);
endmodule
