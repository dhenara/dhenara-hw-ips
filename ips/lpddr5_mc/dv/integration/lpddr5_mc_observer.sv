// Raw observations at the actual source and external digital PHY sampling
// edges. No completion flag is converted into a physical-memory witness.
module lpddr5_mc_observer #(
  parameter int PORTS=2,CHANNELS=2,DATA_BITS=128,
  parameter string FILE_ARGUMENT="observations=%s"
) (
  input logic clk_ck_i,rst_ni,
  input logic [PORTS-1:0] axi_clk_i,axi_rst_ni,
  input logic [PORTS-1:0] awvalid_i,arvalid_i,wvalid_i,awready_i,arready_i,wready_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_address_t [PORTS-1:0] aw_i,ar_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_write_t [PORTS-1:0] w_i,
  input logic [PORTS-1:0] bvalid_i,rvalid_i,bready_i,rready_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_b_t [PORTS-1:0] b_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_axi_read_t [PORTS-1:0] r_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_ca_t [CHANNELS-1:0] ca_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_wck4_t [CHANNELS-1:0] wck_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_write_bus4_t [CHANNELS-1:0] write_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_read_request4_t [CHANNELS-1:0] read_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_return4_t [CHANNELS-1:0] return_i,
  input logic finish_i
);
  int stream,ck=0;
  string path;
  // Keep multiplication real before conversion. The qualified Verilator
  // version truncates $realtime first inside a direct integer cast, losing
  // the fractional source-edge time at CK800 and independent AXI clocks.
  function automatic longint timestamp_ps();
    realtime captured;
    captured=$realtime;
    return longint'(captured*1000.0);
  endfunction
  initial begin
    if($value$plusargs(FILE_ARGUMENT,path)) begin
      stream=$fopen(path,"w");
      assert(stream!=0) else $fatal(1,"cannot open raw observation trace");
      $fdisplay(stream,"{\"kind\":\"header\",\"version\":1,\"ports\":%0d,\"channels\":%0d,\"bus_bytes\":%0d}",PORTS,CHANNELS,DATA_BITS/8);
    end
  end
  for(genvar p=0;p<PORTS;p++) begin : source
    initial forever begin
      @(posedge axi_clk_i[p]);
      if(stream!=0 && rst_ni && axi_rst_ni[p]) begin
        if(awvalid_i[p] && awready_i[p])
          $fdisplay(stream,"{\"kind\":\"aw\",\"ps\":%0d,\"port\":%0d,\"id\":%0d,\"address\":\"0x%h\",\"length\":%0d,\"size\":%0d,\"burst\":%0d,\"lock\":%0d}",
            timestamp_ps(),p,aw_i[p].id,aw_i[p].address,aw_i[p].len,aw_i[p].size,aw_i[p].burst,aw_i[p].lock);
        if(arvalid_i[p] && arready_i[p])
          $fdisplay(stream,"{\"kind\":\"ar\",\"ps\":%0d,\"port\":%0d,\"id\":%0d,\"address\":\"0x%h\",\"length\":%0d,\"size\":%0d,\"burst\":%0d,\"lock\":%0d}",
            timestamp_ps(),p,ar_i[p].id,ar_i[p].address,ar_i[p].len,ar_i[p].size,ar_i[p].burst,ar_i[p].lock);
        if(wvalid_i[p] && wready_i[p])
          $fdisplay(stream,"{\"kind\":\"w\",\"ps\":%0d,\"port\":%0d,\"data\":\"0x%h\",\"strobe\":\"0x%h\",\"last\":%0d}",
            timestamp_ps(),p,w_i[p].data[DATA_BITS-1:0],w_i[p].strobe[DATA_BITS/8-1:0],w_i[p].last);
        if(bvalid_i[p])
          $fdisplay(stream,"{\"kind\":\"b\",\"ps\":%0d,\"port\":%0d,\"id\":%0d,\"response\":%0d,\"ready\":%0d}",
            timestamp_ps(),p,b_i[p].id,b_i[p].response,bready_i[p]);
        if(rvalid_i[p])
          $fdisplay(stream,"{\"kind\":\"r\",\"ps\":%0d,\"port\":%0d,\"id\":%0d,\"data\":\"0x%h\",\"response\":%0d,\"last\":%0d,\"ready\":%0d}",
            timestamp_ps(),p,r_i[p].id,r_i[p].data[DATA_BITS-1:0],r_i[p].response,r_i[p].last,rready_i[p]);
      end
    end
  end
  initial forever begin
    @(posedge clk_ck_i);
    if(stream!=0 && rst_ni) begin
      for(int c=0;c<CHANNELS;c++)
        $fdisplay(stream,"{\"kind\":\"phy\",\"ps\":%0d,\"ck\":%0d,\"channel\":%0d,\"cs\":%0d,\"ca\":%0d,\"wck\":\"0x%h\",\"write\":\"0x%h\",\"read\":\"0x%h\",\"return\":\"0x%h\"}",
          timestamp_ps(),ck,c,ca_i[c].cs,ca_i[c].address,wck_i[c],write_i[c],read_i[c],return_i[c]);
      ck++;
    end
    if(stream!=0 && finish_i) begin
      $fdisplay(stream,"{\"kind\":\"end\",\"ps\":%0d}",timestamp_ps());$fclose(stream);stream=0;
    end
  end
endmodule
