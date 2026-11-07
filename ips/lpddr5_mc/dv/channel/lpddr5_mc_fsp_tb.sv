module lpddr5_mc_fsp_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0,rst_n=0,reset_fsp=0,request=0,drained=1,wck_disabled=1,init_complete=1;
  logic [31:0] budget=3000;
  lpddr5_mc_profile_t current_profile,target_profile,frozen;
  logic [1:0] target_fsp=1,fsp,ratio;
  logic [4:0] frequency;
  lpddr5_mc_ca_t ca;
  logic init_start,busy,declined,fault,clock_done,ready,publish=0,published;
  logic [31:0] publish_epoch=9;
  string vectors;
  lpddr5_mc_fsp dut(.clk_ck_i(clk),.rst_ni(rst_n),.destructive_reset_i(reset_fsp),.request_i(request),.drained_i(drained),.wck_disabled_i(wck_disabled),
    .refresh_budget_i(budget),.current_profile_i(current_profile),.target_profile_i(target_profile),.active_fsp_i(2'd0),.target_fsp_i(target_fsp),
    .current_frequency_i(5'd1),.target_frequency_i(5'd0),.active_epoch_i(32'd8),.target_epoch_i(32'd9),.init_complete_i(init_complete),.init_start_o(init_start),
    .fsp_o(fsp),.ratio_o(ratio),.frequency_o(frequency),.ca_o(ca),.busy_o(busy),.declined_o(declined),.fault_o(fault),.clock_change_done_o(clock_done),
    .target_ready_o(ready),.publish_i(publish),.publish_epoch_i(publish_epoch),.published_o(published),.frozen_target_o(frozen));
  task automatic tick; #1; clk=1; #1; clk=0; #1; endtask
  task automatic reset;
    rst_n=0; tick(); rst_n=1; request=0; drained=1; wck_disabled=1; init_complete=1; publish=0; publish_epoch=9;
  endtask
  task automatic run_case(input int kind);
    int fd,command_count,pending_address,previous_command,last_op,release_edge,observed_done;
    logic saw_decline;
    logic pair_pending;
    logic [127:0] written;
    logic [7:0] expected_value;
    lpddr5_mc_profile_t snapshot;
    assert(kind>=0 && kind<=5) else $fatal(1,"kind range");
    reset(); snapshot=target_profile; saw_decline=0;
    fd=$fopen($sformatf("%s/fsp-%0d.txt",vectors,kind),"w"); assert(fd!=0) else $fatal(1,"trace file");
    request=1; tick(); request=0;
    command_count=0; pair_pending=0; written=0; previous_command=-100; last_op=-1; release_edge=-1; observed_done=-1;
    $fwrite(fd,"%0d %0d %0d %0d %0d %0d %0d\n",init_start,init_complete,frequency,(ratio==1 ? 2 : ratio==2 ? 4 : 0),fsp,clock_done,wck_disabled);
    for(int edge_value=1;edge_value<750;edge_value++) begin
      if(kind==1) init_complete=1;
      else if(kind==2) init_complete=edge_value<4;
      else if(kind==3 && edge_value==20) init_complete=1;
      else if(edge_value==1) init_complete=0;
      if(kind!=4 && release_edge>=0 && edge_value>=release_edge+2) init_complete=1;
      if(kind==5 && edge_value==20) wck_disabled=0;
      // Mutation of the inactive bank cannot modify the frozen transaction.
      if(edge_value==8) target_profile='0;
      #1;
      if(ca.cs!=0) begin
        assert(!fault && ca.cs==3 && frozen==snapshot) else $fatal(1,"FSP command ownership/snapshot");
        if(pair_pending) begin
          assert(ca.address[5:0]==8 && edge_value==previous_command+1) else $fatal(1,"MRW pair");
          expected_value=snapshot.mr[pending_address];
          if(command_count==0) expected_value={snapshot.mr[16][7:4],2'd0,target_fsp};
          if(command_count==18) begin expected_value={snapshot.mr[16][7:4],target_fsp,target_fsp}; last_op=edge_value; end
          assert({ca.address[6],ca.address[13:7]}==expected_value) else $fatal(1,"target MR value");
          pair_pending=0; command_count++;
        end else begin
          assert(ca.address[6:0]==88 && edge_value-previous_command>=12) else $fatal(1,"MRW period");
          pending_address=int'(ca.address[13:7]); pair_pending=1;
          assert(pending_address>=0 && pending_address<128) else $fatal(1,"MR address width");
          if(command_count==0 || command_count==18) assert(pending_address==16) else $fatal(1,"FSP control order");
          else begin
            assert(!written[pending_address] && pending_address inside {1,2,3,10,11,12,14,15,17,18,19,20,22,24,30,41,58}) else $fatal(1,"missing/duplicate target MR");
            written[pending_address]=1;
          end
        end
        previous_command=edge_value;
      end
      tick();
      if(!init_start && release_edge<0) release_edge=edge_value;
      if(clock_done && observed_done<0) observed_done=edge_value;
      $fwrite(fd,"%0d %0d %0d %0d %0d %0d %0d\n",init_start,init_complete,frequency,(ratio==1 ? 2 : ratio==2 ? 4 : 0),fsp,clock_done,wck_disabled);
      if(declined) saw_decline=1;
      if(fault || ready || declined) break;
    end
    repeat(2) begin
      tick();
      $fwrite(fd,"%0d %0d %0d %0d %0d %0d %0d\n",init_start,init_complete,frequency,(ratio==1 ? 2 : ratio==2 ? 4 : 0),fsp,clock_done,wck_disabled);
    end
    $fclose(fd); target_profile=snapshot;
    if(kind==0) begin
      assert(ready && !fault && busy && command_count==19 && observed_done-last_op>=207 && release_edge>observed_done) else $fatal(1,"FSP completion/guard");
      publish=1; tick(); publish=0; assert(published && !busy && !fault) else $fatal(1,"atomic publish");
    end else if(kind==1) begin
      assert(saw_decline && !fault && !busy && command_count==0 && fsp==0 && frequency==1 && ratio==lpddr5_mc_dfi_ratio(current_profile.ratio)) else $fatal(1,"decline changed operating point");
    end else assert(fault && !ready && !published) else $fatal(1,"missing handshake fault kind=%0d",kind);
  endtask
  initial begin
    if(!$value$plusargs("vectors=%s",vectors)) $fatal(1,"vectors missing");
    current_profile=fixture_profile(0,0,0,0,0,0); target_profile=fixture_profile(1,0,0,0,0,0);
    for(int kind=0;kind<6;kind++) run_case(kind);
    $display("PASS FSP: frozen target MRs, paired programming, long guard, fresh low/high handshake, decline, late/early/stuck/WCK faults and epoch publication"); $finish;
  end
endmodule
