// Small timed regression of the exact wrapper capture skeleton and unchanged
// source observer. Payload callbacks are probes, not controller qualification.
module wrapper_capture_tb;
  import lpddr5_mc_contract_pkg::*;
  localparam int PORTS=2,CHANNELS=2;
  logic clk=0;
  logic [1:0] axi_clk=0,monitor_rst_n='1;
  always #5 clk<=~clk;
  always #5 axi_clk[0]<=~axi_clk[0];
  always #3.5 axi_clk[1]<=~axi_clk[1];
  bit observe_traffic=1,finish_observations=0,legacy=0,stop_requested=0,fired=0;
  /* verilator lint_off SYNCASYNCNET */
  logic wm_core_reset_n=1;
  /* verilator lint_on SYNCASYNCNET */
  int counter=0,wm_stream=0,wm_tick[CHANNELS],wm_edge_tick[CHANNELS],reset_stage=0;
  int stop_ps=20000,reset_mode=0;
  string path;
  function automatic longint timestamp_ps();
    realtime captured;
    captured=$realtime;return longint'(captured*1000.0);
  endfunction
  always @(posedge clk or negedge wm_core_reset_n) begin
    if(!wm_core_reset_n) counter<=0;
    else if(stop_requested && !fired && timestamp_ps()>64'(stop_ps)) begin counter<=counter+1;fired<=1;end
  end
  lpddr5_mc_observer #(.OBSERVE_CYCLES(1)) observer (
    .clk_ck_i(clk),.rst_ni(observe_traffic),.axi_clk_i(axi_clk),
    .axi_rst_ni(monitor_rst_n & {2{legacy || wm_axi_capture}}),
    .awvalid_i('0),.arvalid_i('0),.wvalid_i('0),.awready_i('0),.arready_i('0),.wready_i('0),
    .aw_i('0),.ar_i('0),.w_i('0),.bvalid_i('0),.rvalid_i('0),.bready_i('0),.rready_i('0),.b_i('0),.r_i('0),
    .ca_i('0),.wck_i('0),.write_i('0),.read_i('0),.return_i('0),.finish_i(finish_observations));
  for(genvar c=0;c<CHANNELS;c++) begin : wm_observers
    initial forever begin
      @(negedge clk);
      if(legacy ? !finish_observations : wm_accept_sample()) begin
        $fdisplay(wm_stream,"{\"kind\":\"sample\",\"ps\":%0d,\"channel\":%0d,\"tick\":%0d}",timestamp_ps(),c,wm_tick[c]);
        wm_tick[c]++;
      end
    end
    task automatic capture_edge;
      $fdisplay(wm_stream,"{\"kind\":\"edge\",\"ps\":%0d,\"channel\":%0d,\"tick\":%0d}",timestamp_ps(),c,wm_tick[c]-1);
      $fdisplay(wm_stream,"{\"kind\":\"management_edge\",\"ps\":%0d,\"channel\":%0d,\"tick\":%0d}",timestamp_ps(),c,wm_tick[c]-1);
      $fdisplay(wm_stream,"{\"kind\":\"window_edge\",\"ps\":%0d,\"channel\":%0d,\"tick\":%0d}",timestamp_ps(),c,wm_tick[c]-1);
    endtask
  end
  task automatic wm_capture_address(input int p);
    if(!legacy || !finish_observations)
      $fdisplay(wm_stream,"{\"kind\":\"address\",\"ps\":%0d,\"port\":%0d}",timestamp_ps(),p);
  endtask
  task automatic wm_capture_host;
    if(!legacy || !finish_observations)
      $fdisplay(wm_stream,"{\"kind\":\"host_edge\",\"ps\":%0d,\"tick\":%0d,\"count\":%0d,\"reset_n\":%0d,\"handshake\":%0d}",
        timestamp_ps(),wm_host_edge_tick-1,counter,wm_core_reset_n,int'(stop_requested && !fired && timestamp_ps()>64'(stop_ps)));
  endtask
  task automatic wm_capture_reset;
    $fdisplay(wm_stream,"{\"kind\":\"host_reset\",\"ps\":%0d,\"tick\":%0d,\"count\":%0d,\"reset_n\":0}",timestamp_ps(),wm_host_edge_tick-1,counter);
  endtask
  task automatic wm_close_capture;
    assert(observer.stream==0) else $fatal(1,"source not closed");
    $fdisplay(wm_stream,"{\"kind\":\"end\",\"ps\":%0d}",timestamp_ps());$fclose(wm_stream);wm_stream=0;
  endtask
  `include "wrapper_capture.svh"
  initial begin
    if($value$plusargs("stop_ps=%d",stop_ps)) begin end
    if($value$plusargs("reset_mode=%d",reset_mode)) begin end
    legacy=$test$plusargs("legacy");
    assert($value$plusargs("capture=%s",path)) else $fatal;
    wm_stream=$fopen(path,"w");assert(wm_stream!=0) else $fatal;
    for(int c=0;c<CHANNELS;c++) begin wm_tick[c]=0;wm_edge_tick[c]=0;end
    #(real'(stop_ps)/1000.0);stop_requested=1;
    if(legacy) begin
      #0.001;finish_observations=1;@(posedge clk);#0.001;wm_close_capture();
    end else wrapper_stop_observations();
    assert(legacy || wm_capture_closed) else $fatal;
    $display("WRAPPER_CAPTURE_PASS stop_ps=%0d reset=%0d legacy=%0d",stop_ps,reset_mode,legacy);$finish;
  end
  always @(posedge clk) begin
    if(stop_requested && reset_mode!=0 && reset_stage==0 && (reset_mode==1 || finish_observations)) begin
      wm_core_reset_n<=0;reset_stage<=1;
    end else if(reset_stage==1) begin wm_core_reset_n<=1;reset_stage<=2;end
  end
  initial begin #200;$fatal(1,"capture test timeout");end
endmodule
