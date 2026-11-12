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
    budget=3000; reset_fsp=0;
  endtask
  task automatic run_case(input int kind);
    int fd,command_count,pending_address,previous_command,last_op,last_restore,release_edge,observed_done,des_until;
    logic saw_decline;
    logic pair_pending;
    logic [127:0] written;
    logic [7:0] expected_value;
    lpddr5_mc_profile_t snapshot;
    assert(kind>=0 && kind<=9) else $fatal(1,"kind range");
    reset(); snapshot=target_profile; saw_decline=0;
    fd=$fopen($sformatf("%s/fsp-%0d.txt",vectors,kind),"w"); assert(fd!=0) else $fatal(1,"trace file");
    request=1; tick(); request=0;
    command_count=0; pair_pending=0; written=0; previous_command=-100; last_op=-1; last_restore=-1; release_edge=-1; observed_done=-1; des_until=0;
    $fwrite(fd,"%0d %0d %0d %0d %0d %0d %0d\n",init_start,init_complete,frequency,(ratio==1 ? 2 : ratio==2 ? 4 : 0),fsp,clock_done,wck_disabled);
    for(int edge_value=1;edge_value<750;edge_value++) begin
      if(kind==1) init_complete=1;
      else if(kind==2) init_complete=edge_value<4;
      else if(kind==3 && edge_value==20) init_complete=1;
      else if(edge_value==1) init_complete=0;
      if(kind!=4 && release_edge>=0 && edge_value>=release_edge+2) init_complete=1;
      if(kind==5 && edge_value==20) wck_disabled=0;
      // Fault on a first half must still emit its reserved MRW2. Exercise
      // both the OP/high-current pair and the newly added normal restore.
      if(kind==6 && command_count==18 && ca.cs!=0) wck_disabled=0;
      if(kind==7 && command_count==19 && ca.cs!=0) wck_disabled=0;
      if((kind==8 && last_op>=0 && edge_value==last_op+10)
          || (kind==9 && last_restore>=0 && edge_value==last_restore+10)) begin
        reset_fsp=1; tick(); reset_fsp=0;
        assert(!busy && !fault && !ready && !published && !init_start && !clock_done && frozen=='0 && ca.cs==0) else $fatal(1,"destructive FSP recovery");
        break;
      end
      // Mutation of the inactive bank cannot modify the frozen transaction.
      if(edge_value==8) target_profile='0;
      #1;
      if(edge_value<des_until) assert(ca.cs==0) else $fatal(1,"FSP/VRCG recovery permits DES only");
      if(ca.cs!=0) begin
        assert((!fault || pair_pending) && ca.cs==3 && frozen==snapshot) else $fatal(1,"FSP command ownership/snapshot");
        if(pair_pending) begin
          assert(ca.address[5:0]==8 && edge_value==previous_command+1) else $fatal(1,"MRW pair");
          expected_value=snapshot.mr[pending_address];
          if(command_count==0) expected_value={current_profile.mr[16][7:4],2'd0,target_fsp};
          if(command_count==18) begin
            expected_value={snapshot.mr[16][7:4],target_fsp,target_fsp}|8'h40;
            last_op=edge_value; des_until=edge_value+201;
          end
          if(command_count==19) begin
            expected_value={snapshot.mr[16][7:4],target_fsp,target_fsp};
            assert(edge_value-1-last_op>=201) else $fatal(1,"restore before tFC");
            last_restore=edge_value; des_until=edge_value+80;
          end
          assert({ca.address[6],ca.address[13:7]}==expected_value) else $fatal(1,"target MR value");
          pair_pending=0; command_count++;
        end else begin
          assert(ca.address[6:0]==88 && edge_value-previous_command>=12) else $fatal(1,"MRW period");
          pending_address=int'(ca.address[13:7]); pair_pending=1;
          assert(pending_address>=0 && pending_address<128) else $fatal(1,"MR address width");
          if(command_count==0 || command_count>=18) assert(pending_address==16) else $fatal(1,"FSP control order");
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
      if(clock_done || ready || published) assert(last_restore>=0 && edge_value-last_restore>=82) else $fatal(1,"ready before normal VRCG pin recovery");
      $fwrite(fd,"%0d %0d %0d %0d %0d %0d %0d\n",init_start,init_complete,frequency,(ratio==1 ? 2 : ratio==2 ? 4 : 0),fsp,clock_done,wck_disabled);
      if(declined) saw_decline=1;
      if((fault && !pair_pending) || ready || declined) break;
    end
    repeat(2) begin
      tick();
      $fwrite(fd,"%0d %0d %0d %0d %0d %0d %0d\n",init_start,init_complete,frequency,(ratio==1 ? 2 : ratio==2 ? 4 : 0),fsp,clock_done,wck_disabled);
    end
    $fclose(fd); target_profile=snapshot;
    if(kind==0) begin
      assert(ready && !fault && busy && command_count==20 && observed_done-last_restore>=82 && release_edge>observed_done) else $fatal(1,"FSP completion/guard");
      publish=1; tick(); publish=0; assert(published && !busy && !fault) else $fatal(1,"atomic publish");
    end else if(kind==1) begin
      assert(saw_decline && !fault && !busy && command_count==0 && fsp==0 && frequency==1 && ratio==lpddr5_mc_dfi_ratio(current_profile.ratio)) else $fatal(1,"decline changed operating point");
    end else if(kind>=8) begin
      assert(!busy && !fault && !ready && !published) else $fatal(1,"reset reactivated stale FSP");
    end else begin
      assert(fault && !ready && !published && !pair_pending && ca.cs==0) else $fatal(1,"missing contained fault kind=%0d",kind);
      publish=1; tick(); publish=0; assert(fault && !published && !ready) else $fatal(1,"fault publication escaped");
    end
  endtask
  task automatic admission_checks;
    lpddr5_mc_profile_t c,t;
    c=current_profile;t=target_profile;
    assert(fsp_admission_budget(c,t)==679) else $fatal(1,"20-pair reserve inventory");
    reset();request=1;budget=678;tick();assert(!busy && !init_start) else $fatal(1,"insufficient reserve admitted");
    budget=679;tick();request=0;assert(busy && init_start) else $fatal(1,"exact reserve declined");
    reset();request=1;budget=680;tick();request=0;assert(busy && init_start) else $fatal(1,"above reserve declined");
    reset();t.dram_ck_tmrd=112;
    assert(fsp_admission_budget(c,t)==711) else $fatal(1,"target restore timing omitted");
    t=target_profile;c.dram_ck_tmrd=32;
    assert(fsp_admission_budget(c,t)==1039) else $fatal(1,"current staging timing omitted");
    // Arithmetic-only stress; these changed profiles remain unadmitted.
    c.dram_ck_tmrd='1;c.dram_ck_tmrw='1;c.dram_ck_tfc='1;c.dram_ck_tckfspe='1;c.dram_ck_tckfspx='1;
    c.management_ck_tinit_start='1;c.management_ck_tinit_complete='1;t=c;
    assert(fsp_admission_budget(c,t)==1507355) else $fatal(1,"reserve overflow/truncation");
    reset();target_profile.mr[16][6]=1;request=1;tick();request=0;
    assert(fault && !init_start && !ready) else $fatal(1,"high-current target broadened admission");
    target_profile=fixture_profile(1,0,0,0,0,0);
    reset();
  endtask
  initial begin
    if(!$value$plusargs("vectors=%s",vectors)) $fatal(1,"vectors missing");
    current_profile=fixture_profile(0,0,0,0,0,0); target_profile=fixture_profile(1,0,0,0,0,0);
    admission_checks();
    for(int kind=0;kind<10;kind++) run_case(kind);
    $display("PASS FSP: frozen target MRs, OP/high-current, DES recovery, normal restore/pin tail, fresh low/high handshake and epoch publication"); $finish;
  end
endmodule
