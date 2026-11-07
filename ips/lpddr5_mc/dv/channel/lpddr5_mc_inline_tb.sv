module lpddr5_mc_inline_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0,rst_n=0,reset_inline=0,enable=1,admission=1;
  logic request_valid=0,request_ready,physical_valid,physical_ready=1,result_valid=0,result_ready;
  logic completion_valid,completion_ready=0,drained,fault,corrected,uncorrectable;
  lpddr5_mc_granule_t request,physical;
  lpddr5_mc_channel_completion_t result_value,completion;
  logic [511:0] memory_data, memory_check, expected;
  int writes,read_count,fail_write;
  logic physical_data_done;
  logic unused_debug;
  assign unused_debug=^{corrected,uncorrectable,physical};
  lpddr5_mc_inline #(.POISON_DEPTH(16)) dut(.clk_ck_i(clk),.rst_ni(rst_n),.destructive_reset_i(reset_inline),
    .enable_i(enable),.admission_i(admission),.boot_epoch_i(32'd1),.config_epoch_i(32'd9),
    .rank_count_i(2'd2),
    .request_valid_i(request_valid),.request_ready_o(request_ready),.request_i(request),.physical_valid_o(physical_valid),.physical_ready_i(physical_ready),
    .physical_o(physical),.result_valid_i(result_valid),.result_ready_o(result_ready),.result_i(result_value),
    .completion_valid_o(completion_valid),.completion_ready_i(completion_ready),.completion_o(completion),.drained_o(drained),.fault_o(fault),
    .corrected_o(corrected),.uncorrectable_o(uncorrectable));
  task automatic tick; #1; clk=1; #1; clk=0; #1; endtask
  task automatic service;
    logic is_check;
    logic [30:0] offset_value;
    offset_value={1'b0,physical.row[14:0],physical.bank_group,physical.bank,physical.column[5:0],5'b0};
    is_check=offset_value>=31'((1073741824/576)*512);
    assert(offset_value==(is_check ? 31'((1073741824/576)*512) : 31'd0)) else $fatal(1,"reserved mapping");
    if(!is_check && physical.is_write==request.is_write) physical_data_done=1;
    result_value='0; result_value.identity.tag=physical.tag; result_value.identity.boot_epoch=1; result_value.identity.config_epoch=9;
    result_value.is_write=physical.is_write; result_value.physical_done=1; result_value.integrity_done=1;
    if (physical.is_write) begin
      writes++;
      if (writes==fail_write) result_value.failed=1;
      else if (is_check) memory_check=physical.data; else memory_data=physical.data;
    end else begin read_count++; result_value.data=is_check ? memory_check : memory_data; end
    tick(); result_valid=1;
    #1; assert(result_ready) else $fatal(1,"physical tracking");
    tick(); result_valid=0;
  endtask
  task automatic transact(input logic write_value,bl64,input logic [63:0] mask_value,input logic [511:0] data_value,input logic failed_value,upper_half=0,corrected_value=0);
    lpddr5_mc_channel_completion_t held;
    physical_data_done=0;
    request='0; request.column=upper_half ? 12'd1 : 12'd0; request.tag=64'(writes)+64'(read_count)+64'd1; request.generation=3;
    request.is_write=write_value; request.is_64_bytes=bl64; request.byte_enable=mask_value; request.data=data_value;
    request_valid=1; #1; assert(request_ready) else $fatal(1,"missing request credit"); tick(); request_valid=0;
    for (int i=0;i<100;i++) begin
      #1;
      if (physical_valid) service();
      else if (completion_valid) break;
      else tick();
    end
    assert(completion_valid && completion.failed==failed_value && completion.corrected==corrected_value && completion.uncorrectable==failed_value && completion.integrity_done && completion.physical_done==physical_data_done
      && completion.identity.tag==request.tag && completion.identity.generation==3 && completion.identity.boot_epoch==1
      && completion.identity.config_epoch==9 && completion.is_write==write_value && completion.response==(failed_value ? 2 : 0)) else $fatal(1,"inline completion");
    if (!write_value && !failed_value) assert(completion.data==expected) else $fatal(1,"data correction/merge");
    held=completion;
    repeat(3) begin tick(); assert(completion_valid && completion==held && !drained && !request_ready) else $fatal(1,"lock through response stall"); end
    completion_ready=1; tick(); completion_ready=0;
    assert(drained && !fault) else $fatal(1,"inline drain");
  endtask
  initial begin
    request='0; result_value='0; memory_data=0; memory_check=0; expected=0; writes=0; read_count=0; fail_write=-1;
    tick(); rst_n=1;
    expected={8{64'h0123456789abcdef}};
    transact(1,1,'1,expected,0);
    transact(0,1,0,0,0);
    expected[7:0]=8'hb7;
    transact(1,1,1,{504'd0,8'hb7},0); transact(0,1,0,0,0);
    // A correctable bit is scrubbed while the granule remains locked.
    memory_data[23]^=1; transact(0,1,0,0,0,0,1);
    assert(memory_data==expected) else $fatal(1,"correction scrub missing");
    // Double error must not be treated as a successful read or partial write.
    memory_data[30]^=1; memory_data[31]^=1;
    transact(0,1,0,0,1); transact(1,1,1,0,1);
    transact(1,1,'1,expected,0);
    // Check-write failure after data update poisons the locked words.
    fail_write=writes+2; transact(1,1,'1,~expected,1); fail_write=-1;
    transact(0,1,0,0,1); transact(1,1,1,0,1);
    transact(1,1,'1,expected,0); transact(0,1,0,0,0);
    expected={256'd0,expected[255:0]}; transact(0,0,0,0,0);
    expected={256'd0,memory_data[511:256]}; transact(0,0,0,0,0,1);
    reset_inline=1; tick(); reset_inline=0; tick(); assert(drained && !fault) else $fatal(1,"destructive reset");
    $display("PASS inline: protected mapping, partial RMW, locked check commits, correction scrub, UE, partial physical failure poison/rewrite, response stalls"); $finish;
  end
endmodule
