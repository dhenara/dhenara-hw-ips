module lpddr5_mc_granule_adapter_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0,rst_n=0,reset_adapter=0,admission=1,request_valid=0,request_ready;
  logic physical_valid,physical_ready=0,result_valid=0,result_ready,completion_valid,completion_ready=0,drained,fault;
  lpddr5_mc_granule_t request,physical,held;
  lpddr5_mc_channel_completion_t result_value,completion,held_completion;
  logic [63:0] stale;
  lpddr5_mc_granule_adapter #(.DEPTH(4)) dut(.clk_ck_i(clk),.rst_ni(rst_n),.destructive_reset_i(reset_adapter),.admission_i(admission),
    .rank_count_i(2'd2),
    .boot_epoch_i(32'd3),.config_epoch_i(32'd7),.request_valid_i(request_valid),.request_ready_o(request_ready),.request_i(request),
    .physical_valid_o(physical_valid),.physical_ready_i(physical_ready),.physical_o(physical),.result_valid_i(result_valid),.result_i(result_value),.result_ready_o(result_ready),
    .completion_valid_o(completion_valid),.completion_ready_i(completion_ready),.completion_o(completion),.drained_o(drained),.fault_o(fault),.stale_results_o(stale));
  task automatic tick; #1; clk=1; #1; clk=0; #1; endtask
  initial begin
    lpddr5_mc_granule_t sent[160];
    int due[160],stage_value[160];
    int admitted,physical_count,completed,pending_result,n,half_index,selected;
    logic [79:0] seen;
    logic held_valid,held_result_valid;
    logic [63:0] previous_tag;
    request='0;result_value='0;tick();rst_n=1;
    admitted=0;physical_count=0;completed=0;pending_result=0;seen=0;held_valid=0;held_result_valid=0;previous_tag=0;
    for(int i=0;i<160;i++) begin due[i]=0;stage_value[i]=0;sent[i]='0; end
    for(int cycle=0;cycle<3000;cycle++) begin
      request='0;request_valid=admitted<80;request.tag=64'(admitted)+1000;request.generation=16'(admitted)+1;
      request.address=40'(admitted)*64;request.row=18'(admitted/16);request.column=12'((admitted%32)*2);
      request.is_write=1'(admitted%2);request.is_64_bytes=1;request.byte_enable=64'hff00ff00ffff00ff;
      request.data={{4{64'(admitted)+64'hbb00}},{4{64'(admitted)+64'haa00}}};
      physical_ready=cycle%7<4;completion_ready=cycle%5<2;
      result_valid=0;result_value='0;selected=-1;
      for(int i=0;i<160;i++) if(i<physical_count && stage_value[i]<2 && due[i]<=cycle && selected<0) selected=i;
      if(selected>=0) begin
        result_valid=1;result_value.identity.tag=sent[selected].tag;result_value.identity.boot_epoch=3;result_value.identity.config_epoch=7;
        result_value.is_write=sent[selected].is_write;result_value.physical_done=1;result_value.integrity_done=stage_value[selected]==1;
        n=int'(sent[selected].address/64);half_index=int'(sent[selected].address%64)/32;
        result_value.failed=n==13 && half_index==1;
        result_value.response=result_value.failed ? 2 : 0;result_value.data={8{64'(n)+64'hcca000}};
      end
      #1;
      if(held_valid) assert(physical_valid && physical==held) else $fatal(1,"stable physical request");
      held_valid=physical_valid && !physical_ready;held=physical;
      if(held_result_valid) assert(completion_valid && completion==held_completion) else $fatal(1,"stable logical response");
      held_result_valid=completion_valid && !completion_ready;held_completion=completion;
      if(request_valid && request_ready) admitted++;
      if(physical_valid && physical_ready) begin
        assert(physical_count<160 && physical.tag>previous_tag && physical.generation==0) else $fatal(1,"physical identity reuse");
        previous_tag=physical.tag;sent[physical_count]=physical;due[physical_count]=cycle+3;physical_count++;
        n=int'(physical.address/64);half_index=int'(physical.address%64)/32;
        assert(physical.column==12'((n%32)*2+half_index) && physical.row==18'(n/16)) else $fatal(1,"column/split address");
        if(physical.is_write) begin
          assert(!physical.is_64_bytes && physical.byte_enable==(half_index==0 ? 64'hffff00ff : 64'hff00ff00)
            && physical.data==(half_index==0 ? {256'd0,{4{64'(n)+64'haa00}}} : {256'd0,{4{64'(n)+64'hbb00}}})) else $fatal(1,"MWR16 expansion");
        end else assert(physical.is_64_bytes && half_index==0) else $fatal(1,"read expansion");
      end
      if(completion_valid && completion_ready) begin
        n=int'(completion.identity.tag)-1000;
        assert(n>=0 && n<80) else $fatal(1,"logical tag range");
        assert(!seen[n] && completion.identity.generation==16'(n)+1 && completion.identity.boot_epoch==3 && completion.identity.config_epoch==7
          && completion.is_write==1'(n%2) && completion.physical_done && completion.integrity_done && completion.failed==(n==13)
          && completion.response==(n==13 ? 2 : 0)) else $fatal(1,"logical completion/aggregation");
        if(!completion.is_write) assert(completion.data=={8{64'(n)+64'hcca000}}) else $fatal(1,"read payload");
        seen[n]=1;completed++;
      end
      tick(); assert(!fault && result_ready) else $fatal(1,"adapter fault");
      if(selected>=0) begin stage_value[selected]++;due[selected]=cycle+5;pending_result++; end
      if(completed==80) break;
    end
    request_valid=0;result_valid=0;tick();
    assert(completed==80 && physical_count==120 && pending_result==240 && seen=='1 && drained && !fault) else $fatal(1,"long stream beyond tracking depth");
    // A stale result after tracking reuse cannot match a new internal tag.
    result_value='0;result_value.identity.tag=sent[0].tag;result_value.identity.boot_epoch=3;result_value.identity.config_epoch=7;
    result_value.integrity_done=1;result_valid=1;tick();result_valid=0;
    assert(stale==1 && !fault && !completion_valid) else $fatal(1,"stale physical result fencing");
    // Hold the error result while inspecting it; the streaming loop may leave
    // ready asserted and otherwise retire this completion before the check.
    completion_ready=0;
    request='0;request.tag=9000;request.generation=4;request.is_64_bytes=1;request.column=1;request_valid=1;tick();request_valid=0;
    repeat(3) tick();assert(completion_valid && completion.failed && !completion.physical_done && !physical_valid) else $fatal(1,"invalid column side effects");
    completion_ready=1;tick();assert(drained && !fault) else $fatal(1,"invalid request settlement");
    $display("PASS granule adapter: 80 requests through four credits, MWR16 halves, distinct physical tags, two-stage results, failures/stalls/stale fencing and invalid column rejection");$finish;
  end
endmodule
