module lpddr5_mc_stream_tb;
  timeunit 1ps; timeprecision 1ps;
  import lpddr5_mc_contract_pkg::*;
  bit rst_n=0,read_run;
  int half0=1250,half1=1250,phase=333,reset_ps=7111,service=2;
  int transactions=37500;
  initial begin
    void'($value$plusargs("half0=%d",half0));
    void'($value$plusargs("half1=%d",half1));
    void'($value$plusargs("phase=%d",phase));
    void'($value$plusargs("reset_ps=%d",reset_ps));
    void'($value$plusargs("service=%d",service));
    read_run=$test$plusargs("read_run");
    assert(half0>0 && half1>0 && phase>=0 && reset_ps>0 && service inside {1,2,4}) else $fatal(1,"clock/config input");
  end
  initial begin #(reset_ps);rst_n=1;end
  lpddr5_mc_management_fixture management(g[0].clk,rst_n);
  bit [1:0] done=0;
  for(genvar p=0;p<2;p++) begin:g
    // Scalar procedural clocks avoid bit-select clock aliases in Verilator 5.006.
    logic clk=0;
    initial begin
      #1;
      if(p==1) #(phase);
      forever #(p==0 ? half0 : half1) clk=~clk;
    end
    logic awv,awr,wv,wr,arv,arr,bv,br,rv,rr;
    lpddr5_mc_axi_address_t aw,ar;
    lpddr5_mc_axi_write_t w;
    lpddr5_mc_axi_b_t b;
    lpddr5_mc_axi_read_t r;
    int accepts=0,completes=0,read_index=0,write_index=0;
    int max_live=0,w_consecutive=0,r_consecutive=0;
    time last_w=0,last_r=0;
    lpddr5_mc_axi_agent agent(clk,rst_n,awv,awr,aw,wv,wr,w,arv,arr,ar,bv,br,b,rv,rr,r);
    lpddr5_mc_stream_endpoint endpoint(clk,rst_n,awv,wv,arv,br,rr,awr,wr,arr,bv,rv,aw,ar,w,b,r);
    initial begin
      wait(rst_n);
      forever begin
        @(posedge clk);
      if((awv && awr) || (arv && arr)) begin
        accepts++;
        $display("{\"kind\":\"admit\",\"ps\":%0t,\"channel\":%0d,\"tag\":%0d}",$time,p,accepts-1);
      end
      if(wv && wr) begin
        if(last_w!=0 && $time-last_w==2*(p==0 ? time'(half0) : time'(half1))) w_consecutive++;
        last_w=$time;write_index++;
      end
      if(rv && rr) begin
        int slot;
        slot=((read_index/4)%16)*4+read_index%4;
        assert(r.data==256'(slot)+256'h209500 && r.last==(read_index%4==3) && r.id==16'(read_index/4)) else $fatal(1,"stream R data/identity");
        if(last_r!=0 && $time-last_r==2*(p==0 ? time'(half0) : time'(half1))) r_consecutive++;
        last_r=$time;read_index++;
      end
      if((bv && br) || (rv && rr && r.last)) begin
        if(bv && br) assert(b.id==16'(completes)) else $fatal(1,"stream B identity");
        $display("{\"kind\":\"complete\",\"ps\":%0t,\"channel\":%0d,\"tag\":%0d}",$time,p,completes);
        completes++;
      end
      if(accepts-completes>max_live) max_live=accepts-completes;
      assert(accepts-completes<=16) else $fatal(1,"stream credit overflow");
    end
    end
    initial begin
      lpddr5_mc_axi_address_t addresses[];
      lpddr5_mc_axi_write_t words[];
      addresses=new[transactions];words=new[transactions*4];
      for(int i=0;i<transactions;i++) begin
        addresses[i]='0;addresses[i].address=40'(int'((i%16)*64));addresses[i].len=3;addresses[i].size=4;addresses[i].burst=1;addresses[i].id=16'(i);
        for(int j=0;j<4;j++) begin
          words[i*4+j]='0;words[i*4+j].data=256'(int'(i*4+j))+256'h209500;
          words[i*4+j].strobe=32'hffff;words[i*4+j].last=j==3;
        end
      end
      wait(rst_n);
      // Admit a finite queue before data service, proving multiple outstanding
      // requests. Subsequent address/data/response streams run concurrently.
      fork
        begin g[p].agent.stream_address(!read_run,addresses); end
        begin repeat(8) @(posedge clk);if(!read_run) g[p].agent.stream_w(words);end
        begin g[p].agent.stream_responses(!read_run,read_run ? transactions*4 : transactions); end
      join
      g[p].endpoint.check_memory(read_run,transactions);
      assert(accepts==transactions && completes==transactions && max_live>1) else $fatal(1,"stream outstanding/accounting");
      assert(read_run ? r_consecutive>transactions*3 : w_consecutive>transactions*3) else $fatal(1,"stream inserted bubbles");
      $display("{\"kind\":\"stimulus\",\"channel\":%0d,\"max_live\":%0d,\"consecutive_w\":%0d,\"consecutive_r\":%0d,\"half_ps\":%0d}",p,max_live,w_consecutive,r_consecutive,p==0 ? half0 : half1);
      done[p]=1;
    end
  end
  initial begin
    wait(&done);
    // The measured window always contains 100 complete 3000-CK periods.
    if($time<375000000) #(375000000-$time);
    $display("{\"kind\":\"end\",\"ps\":%0t}",$time);
    $display("STREAM_FIXTURE_PASS");$finish;
  end
  initial begin #450000000;$fatal(1,"stream timeout");end
endmodule
