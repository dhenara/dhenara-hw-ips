module lpddr5_mc_system_tb;
  timeunit 1ps; timeprecision 1ps;
  import lpddr5_mc_contract_pkg::*;
  logic ck=0, aclk0=0, aclk1=0, pclk=0, rst_n=0;
  always #625 ck<=~ck;
  always #1250 aclk0<=~aclk0;
  initial begin #333; forever #1375 aclk1=~aclk1; end
  initial begin #101; forever #3500 pclk=~pclk; end
  logic [1:0] awv,awr,wv,wr,arv,arr,bv,br,rv,rr;
  lpddr5_mc_axi_address_t aw[2],ar[2];
  lpddr5_mc_axi_write_t w[2];
  lpddr5_mc_axi_read_t r[2];
  lpddr5_mc_axi_b_t b[2];
  logic [1:0] clocks;
  assign clocks={aclk1,aclk0};
  for(genvar port=0;port<2;port++) begin:g
    lpddr5_mc_axi_agent agent(clocks[port],rst_n,awv[port],awr[port],aw[port],wv[port],wr[port],w[port],arv[port],arr[port],ar[port],bv[port],br[port],b[port],rv[port],rr[port],r[port]);
    lpddr5_mc_fixture_endpoint #(.PORT(port)) endpoint(clocks[port],rst_n,awv[port],wv[port],arv[port],awr[port],wr[port],arr[port],aw[port],ar[port],w[port],bv[port],rv[port],br[port],rr[port],b[port],r[port]);
    lpddr5_mc_observer #(.WIDTH($bits(lpddr5_mc_axi_address_t))) aw_check(clocks[port],rst_n,awv[port],awr[port],aw[port]);
    lpddr5_mc_observer #(.WIDTH($bits(lpddr5_mc_axi_write_t))) w_check(clocks[port],rst_n,wv[port],wr[port],w[port]);
  end
  logic psel,penable,pready;
  lpddr5_mc_apb_request_t preq;
  lpddr5_mc_apb_response_t presp;
  logic [31:0] register_q;
  int wait_q, writes_q, ck_edges;
  lpddr5_mc_apb_agent apb(pclk,rst_n,psel,penable,preq,pready,presp);
  assign pready=psel && penable && wait_q==2;
  assign presp='{data:register_q,error:preq.address!=0 || (!preq.write && preq.strobe!=0)};
  always_ff @(posedge pclk or negedge rst_n) begin
    if(!rst_n) begin wait_q<=0; register_q<=0; writes_q<=0; end
    else if(psel && penable) begin
      if(pready) begin
        wait_q<=0;
        if(preq.write && !presp.error) begin
          for(int i=0;i<4;i++) if(preq.strobe[i]) register_q[i*8+:8]<=preq.data[i*8+:8];
          writes_q<=writes_q+1;
        end
      end else wait_q<=wait_q+1;
    end else wait_q<=0;
  end
  always @(posedge ck or negedge rst_n) if(!rst_n) ck_edges<=0; else ck_edges<=ck_edges+1;
  initial begin
    ck_edges=0;
    $display("{\"kind\":\"header\",\"version\":1,\"bus_bytes\":16,\"ports\":2,\"capacity\":64,\"source\":\"multiclock_rtl_fixture\"}");
    #7111; rst_n=1;
    fork
      begin
        lpddr5_mc_axi_address_t a;
        lpddr5_mc_axi_write_t data;
        lpddr5_mc_axi_b_t result_b;
        lpddr5_mc_axi_read_t result_r;
        result_b='0; result_r='0;
        a='0; a.id=16'hfffe; a.len=3; a.size=4; a.burst=1;
        g[0].agent.address(1,a);
        repeat(3) @(posedge aclk0); // AW hazard exists before first W.
        for(int i=0;i<4;i++) begin
          data='0; data.data=(256'(i)+256'h1234); data.strobe=32'hffff; data.last=i==3;
          g[0].agent.write_beat(data);
        end
        g[0].agent.response_b(5,result_b);
        assert(result_b.id==a.id && result_b.response==0) else $fatal(1,"fixture B");
        g[0].agent.address(0,a);
        for(int i=0;i<4;i++) begin
          g[0].agent.response_r(i%3,result_r);
          assert(result_r.data==(256'(i)+256'h1234) && result_r.last==(i==3)) else $fatal(1,"fixture R");
        end
      end
      begin
        lpddr5_mc_axi_address_t a;
        lpddr5_mc_axi_write_t data;
        lpddr5_mc_axi_b_t result_b;
        lpddr5_mc_axi_read_t result_r;
        result_b='0; result_r='0;
        a='0; a.address=64; a.id=16'hffff; a.len=3; a.size=4; a.burst=1;
        g[1].agent.address(1,a);
        for(int i=0;i<4;i++) begin
          data='0; data.data=(256'(i)+256'h5678); data.strobe=32'hffff; data.last=i==3;
          g[1].agent.write_beat(data);
        end
        g[1].agent.response_b(3,result_b);
        assert(result_b.id==a.id && result_b.response==0) else $fatal(1,"fixture B");
        g[1].agent.address(0,a);
        for(int i=0;i<4;i++) begin
          g[1].agent.response_r((i+1)%3,result_r);
          assert(result_r.data==(256'(i)+256'h5678) && result_r.last==(i==3)) else $fatal(1,"fixture R");
        end
      end
      begin
        lpddr5_mc_apb_request_t request;
        lpddr5_mc_apb_response_t response;
        request='0; request.write=1; request.data=32'hffffffff; request.strobe=4'b0101;
        apb.transfer(request,response);
        assert(!response.error) else $fatal(1,"APB write");
        request.write=0; request.strobe=0;
        apb.transfer(request,response);
        assert(!response.error && response.data==32'h00ff00ff) else $fatal(1,"APB byte strobes");
        request.address=4;
        apb.transfer(request,response);
        assert(response.error) else $fatal(1,"APB invalid address");
      end
    join
    assert(writes_q==1 && ck_edges>40) else $fatal(1,"fixture clock/APB accounting");
    $display("{\"kind\":\"end\",\"ps\":%0t}",$time);
    $display("SYSTEM_FIXTURE_PASS");
    $finish;
  end
  initial begin #2000000; $fatal(1,"fixture timeout"); end
endmodule
