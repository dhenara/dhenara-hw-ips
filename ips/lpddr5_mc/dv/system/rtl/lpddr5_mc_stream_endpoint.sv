// Eight-entry finite environment endpoint. No LPDDR5/CDC engine is present.
// Full write validation precedes effects. Separate AW/B and AR storage allows
// queued transactions and sustained data while responses are continuously ready.
module lpddr5_mc_stream_endpoint (
  input logic clk_i, rst_ni,
  input logic awv,wv,arv,br,rr,
  output logic awr,wr,arr,bv,rv,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_address_t aw,ar,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_write_t w,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_b_t b,
  output lpddr5_mc_contract_pkg::lpddr5_mc_axi_read_t r
);
  timeunit 1ps; timeprecision 1ps;
  import lpddr5_mc_contract_pkg::*;
  lpddr5_mc_axi_address_t aq[8],rq[8];
  lpddr5_mc_axi_b_t bq[8];
  logic [127:0] mem[64], buffer_q[4];
  int aw_in,aw_out,b_in,b_out,ar_in,ar_out,wi,ri;
  assign awr=aw_in-aw_out<8;
  assign wr=aw_in>aw_out && b_in-b_out<8;
  assign arr=ar_in-ar_out<8;
  assign bv=b_in>b_out;
  assign rv=ar_in>ar_out;
  assign b=bq[b_out%8];
  assign r='{data:{128'b0,mem[(int'(rq[ar_out%8].address)/16+ri)%64]},id:rq[ar_out%8].id,response:2'b0,last:ri==3};
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if(!rst_ni) begin
      aw_in<=0;aw_out<=0;b_in<=0;b_out<=0;ar_in<=0;ar_out<=0;wi<=0;ri<=0;
      for(int i=0;i<64;i++) mem[i]<=128'(i)+128'h209500;
      for(int i=0;i<8;i++) begin aq[i]<='0;rq[i]<='0;bq[i]<='0;end
      for(int i=0;i<4;i++) buffer_q[i]<='0;
    end else begin
      if(awv && awr) begin
        assert(aw.len==3 && aw.size==4 && aw.address<1024) else $fatal(1,"stream AW profile");
        aq[aw_in%8]<=aw;aw_in<=aw_in+1;
      end
      if(wv && wr) begin
        assert(w.strobe==32'hffff && w.last==(wi==3)) else $fatal(1,"stream W validation");
        assert(w.data==256'(int'(4*int'(aq[aw_out%8].id)+wi))+256'h209500) else $fatal(1,"stream W data/identity");
        buffer_q[wi]<=w.data[127:0];
        if(wi==3) begin
          for(int j=0;j<4;j++) mem[(int'(aq[aw_out%8].address)/16+j)%64]<=j==3 ? w.data[127:0] : buffer_q[j];
          bq[b_in%8]<='{id:aq[aw_out%8].id,response:2'b0};b_in<=b_in+1;
          aw_out<=aw_out+1;wi<=0;
        end else wi<=wi+1;
      end
      if(bv && br) b_out<=b_out+1;
      if(arv && arr) begin
        assert(ar.len==3 && ar.size==4 && ar.address<1024) else $fatal(1,"stream AR profile");
        rq[ar_in%8]<=ar;ar_in<=ar_in+1;
      end
      if(rv && rr) begin
        if(ri==3) begin ar_out<=ar_out+1;ri<=0;end
        else ri<=ri+1;
      end
    end
  end
  task automatic check_memory(input bit read_run,input int count);
    int last_transaction,word_index;
    for(int i=0;i<64;i++) begin
      last_transaction=count-1-((count-1-i/4)%16);
      word_index=read_run ? i : last_transaction*4+i%4;
      assert(mem[i]==128'(word_index)+128'h209500) else $fatal(1,"stream physical memory mismatch");
    end
  endtask
  lpddr5_mc_observer #(.WIDTH($bits(lpddr5_mc_axi_b_t))) bc(clk_i,rst_ni,bv,br,b);
  lpddr5_mc_observer #(.WIDTH($bits(lpddr5_mc_axi_read_t))) rc(clk_i,rst_ni,rv,rr,r);
endmodule
