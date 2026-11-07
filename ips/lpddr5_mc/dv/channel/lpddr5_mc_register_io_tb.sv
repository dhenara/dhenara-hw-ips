module lpddr5_mc_register_io_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0,rst_n=0,reset_io=0,request_valid=0,request_ready,request_rank=0,request_zq=0,exclusive=1;
  logic [6:0] address=4;
  logic busy,result_valid,result_ready=0,result_rank,result_zq,result_failed,fault;
  logic [6:0] result_address;
  logic [7:0] result_value;
  lpddr5_mc_profile_t profile;
  lpddr5_mc_ca_t ca;
  lpddr5_mc_wck4_t wck;
  lpddr5_mc_read_request4_t read_request;
  lpddr5_mc_return4_t return_value;
  logic unused_debug;
  assign unused_debug=^{wck,read_request};
  lpddr5_mc_register_io dut(.clk_ck_i(clk),.rst_ni(rst_n),.destructive_reset_i(reset_io),.profile_i(profile),.boot_epoch_i(32'd4),.config_epoch_i(32'd8),
    .request_valid_i(request_valid),.request_ready_o(request_ready),.request_rank_i(request_rank),.request_zq_i(request_zq),.request_address_i(address),.exclusive_i(exclusive),
    .busy_o(busy),.result_valid_o(result_valid),.result_ready_i(result_ready),.result_rank_o(result_rank),.result_zq_o(result_zq),.result_failed_o(result_failed),
    .result_address_o(result_address),.result_value_o(result_value),.ca_o(ca),.wck_o(wck),.read_request_o(read_request),.return_i(return_value),.fault_o(fault));
  task automatic tick; #1; clk=1; #1; clk=0; #1; endtask
  task automatic run_case(input int ratio_two,always_on,kind);
    logic [7:0] enable_history[3];
    logic [7:0] held_value;
    int mrr_at,zq_at,word_count,command_count;
    assert(ratio_two inside {0,1} && always_on inside {0,1}) else $fatal(1,"profile selector");
    profile=fixture_profile(1'(ratio_two),0,0,1'(always_on),0,0);
    rst_n=0; request_valid=0; result_ready=0; return_value='0; tick(); rst_n=1;
    for(int h=0;h<3;h++) enable_history[h]=0;
    request_rank=1; request_zq=kind==1; address=kind==0 ? 7'd43 : 7'd4;
    exclusive=0; #1; assert(!request_ready) else $fatal(1,"exclusive pin ownership"); exclusive=1;
    request_valid=1; tick(); request_valid=0;
    mrr_at=-1; zq_at=-1; word_count=0; command_count=0;
    for(int cycle=0;cycle<500;cycle++) begin
      return_value='0;
      for(int lane=0;lane<4;lane++) if(enable_history[1][lane*2+:2]!=0 && kind!=2) begin
        return_value.valid[lane]=enable_history[1][lane*2+:2];
        // First eight UI have defined low-byte opcode data; later UI and
        // the high byte are deliberately different undefined test values.
        return_value.data[lane]=word_count<4 ? 32'hda35bc35 : 32'h12345678;
        if(kind==3 && word_count==0) return_value.dnv[lane]=3;
        word_count++;
      end
      #1;
      if(ca.cs!=0) begin
        assert(ca.cs==2) else $fatal(1,"register rank selection");
        command_count++;
        case(ca.address[6:0])
          44: assert(mrr_at<0) else $fatal(1,"sync after MRR");
          24: begin assert(ca.address[13:7]==address && mrr_at<0) else $fatal(1,"MRR address"); mrr_at=cycle; end
          124: assert(mrr_at>=0 && ca.address[13:7]==0) else $fatal(1,"WS_OFF encoding/origin");
          112: begin assert(request_zq && ca.address[13:7]==6) else $fatal(1,"MPC ZQ opcode"); zq_at=cycle; end
          default: $fatal(1,"unexpected register CA %h",ca.address);
        endcase
      end
      for(int h=2;h>0;h--) enable_history[h]=enable_history[h-1];
      enable_history[0]=read_request.enable;
      tick();
      if(result_valid || fault) begin
        if(kind==2) assert(fault && !result_valid) else $fatal(1,"missing return fault");
        else begin
          assert(result_valid && !fault && result_rank==request_rank && result_zq==request_zq && result_address==address) else $fatal(1,"register result identity");
          if(request_zq) assert(cycle-zq_at>=24 && command_count==1) else $fatal(1,"ZQ recovery");
          else assert(result_value==8'h35 && result_failed==(kind==3) && word_count==8 && cycle-mrr_at>=int'(profile.dram_ck_tmrr)) else $fatal(1,"MRR opcode/return/guard");
          held_value=result_value;
          repeat(8) begin tick(); assert(result_valid && result_value==held_value && busy && ca.cs==0) else $fatal(1,"stable register response"); end
          result_ready=1; tick(); result_ready=0; assert(!busy) else $fatal(1,"register retirement");
        end
        return;
      end
    end
    $fatal(1,"register IO timeout");
  endtask
  initial begin
    profile='0; return_value='0;
    for(int ratio_two=0;ratio_two<2;ratio_two++) for(int always_on=0;always_on<2;always_on++)
      for(int kind=0;kind<4;kind++) run_case(ratio_two,always_on,kind);
    $display("PASS register IO: actual MRR/return data, defined UI, rank, WCK/stop, ZQ latch/recovery, result stalls, DNV and missing-return faults"); $finish;
  end
endmodule
