module lpddr5_mc_data_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0, rst_n=0, abort_request=0;
  lpddr5_mc_profile_t profile;
  logic launch_valid=0, launch_ready, done_valid, done_ready=0;
  lpddr5_mc_granule_t request;
  lpddr5_mc_channel_identity_t identity;
  lpddr5_mc_channel_completion_t done;
  lpddr5_mc_write_bus4_t write_bus, expected_write;
  lpddr5_mc_read_request4_t read_bus, expected_read;
  lpddr5_mc_return4_t return_bus;
  logic drained, fault;
  logic [63:0] pin_end;
  string vector_directory;
  int cycle_number, read_count, expected_done;
  logic [511:0] stable_data;
  logic inject_dnv=0;
  int inject_link=0;
  logic [127:0] ret_data, wr_data;
  logic [7:0] ret_valid, wr_enable;
  logic [15:0] ret_dnv, ret_parity, wr_mask, wr_parity, rd_cs;
  logic [7:0] wr_cs, rd_enable;
  lpddr5_mc_data #(.DEPTH(4)) dut (
    .clk_ck_i(clk), .rst_ni(rst_n), .abort_i(abort_request), .profile_i(profile),
    .launch_valid_i(launch_valid), .launch_ready_o(launch_ready), .launch_i(request), .identity_i(identity),
    .done_valid_o(done_valid), .done_ready_i(done_ready), .done_o(done),
    .write_o(write_bus), .read_request_o(read_bus), .return_i(return_bus),
    .drained_o(drained), .fault_o(fault), .last_pin_half_wck_o(pin_end)
  );
  `include "profiles.svh"
  task automatic edge_tick;
    #1; clk=1; #1; clk=0; #1;
  endtask
  task automatic run_case(input int number, profile_number, write_access, beats, rank_number,
      input logic [511:0] data, input logic [63:0] enables);
    int fd, snapshots;
    logic injected;
    assert(rank_number inside {0,1}) else $fatal(1,"test rank");
    rst_n=0; edge_tick(); set_profile(profile_number);
    request='0; identity='0; return_bus='0;
    request.tag=64'(number)+64'd1; request.generation=1;
    request.data=data; request.byte_enable=enables;
    request.is_write=1'(write_access); request.is_64_bytes=beats==32;
    request.rank_index=1'(rank_number);
    identity.tag=request.tag; identity.generation=1; identity.config_epoch=7; identity.boot_epoch=3;
    rst_n=1; launch_valid=1; done_ready=0;
    #1; assert(launch_ready) else $fatal(1,"no launch credit");
    edge_tick(); launch_valid=0;
    assert(!drained) else $fatal(1,"early drain");
    fd=$fopen($sformatf("%s/data-%0d.txt",vector_directory,number),"r");
    assert(fd!=0) else $fatal(1,"vector open"); snapshots=0;injected=0;
    while (!$feof(fd)) begin
      read_count=$fscanf(fd,"%h %h %h %h %h %h %h %h %h %h %h %h %h\n",cycle_number,
        ret_data,ret_valid,ret_dnv,ret_parity,wr_data,wr_mask,wr_enable,wr_cs,wr_parity,rd_enable,rd_cs,expected_done);
      assert(expected_done inside {0,1}) else $fatal(1,"expected completion bit");
      if (read_count!=13) $fatal(1,"vector parse");
      return_bus.data=ret_data; return_bus.valid=ret_valid; return_bus.dnv=ret_dnv; return_bus.link_parity=ret_parity;
      if (inject_dnv && ret_valid!=0) return_bus.dnv='1;
      if(inject_link!=0) for(int lane=0;lane<4;lane++) if(return_bus.valid[lane][0] && !injected) begin
        return_bus.data[lane][0]^=1'b1;
        if(inject_link==2) return_bus.data[lane][1]^=1'b1;
        injected=1;
      end
      expected_write.data=wr_data; expected_write.mask=wr_mask; expected_write.enable=wr_enable;
      expected_write.cs=wr_cs; expected_write.link_parity=wr_parity;
      expected_read.enable=rd_enable; expected_read.cs=rd_cs;
      #1;
      assert(write_bus===expected_write) else $fatal(1,"write case=%0d CK=%0d actual=%h expected=%h",number,cycle_number,write_bus,expected_write);
      assert(read_bus===expected_read) else $fatal(1,"read request case=%0d CK=%0d",number,cycle_number);
      assert(done_valid==1'(expected_done)) else $fatal(1,"completion edge case=%0d CK=%0d valid=%b expected=%b",number,cycle_number,done_valid,1'(expected_done));
      if (done_valid) begin
        assert(done.identity==identity && done.physical_done && done.integrity_done && done.failed==(inject_dnv || inject_link==2) && done.corrected==(inject_link==1) && done.uncorrectable==(inject_dnv || inject_link==2) && done.is_write==1'(write_access) && done.response==((inject_dnv || inject_link==2) ? 2'b10 : 2'b00)
          && 64'(cycle_number)*64'(2*int'(profile.ratio))>=pin_end) else $fatal(1,"identity/status case=%0d injection=%0d failed=%0d corrected=%0d uncorrectable=%0d response=%0d",number,inject_link,done.failed,done.corrected,done.uncorrectable,done.response);
        if(inject_link!=0) assert(done.syndrome!=0) else $fatal(1,"missing link diagnostic syndrome");
        if (write_access==0 && inject_link!=2) assert(done.data==data) else $fatal(1,"read data case=%0d actual=%h expected=%h",number,done.data,data);
        if (snapshots==0) stable_data=done.data;
        else assert(done.data==stable_data) else $fatal(1,"unstable completion");
        snapshots++;
      end
      edge_tick();
      assert(!fault) else $fatal(1,"data fault case=%0d CK=%0d",number,cycle_number);
    end
    $fclose(fd);
    assert(snapshots>=3) else $fatal(1,"backpressure not exercised");
    done_ready=1; edge_tick(); done_ready=0;
    assert(drained && !done_valid) else $fatal(1,"did not retire");
  endtask
  task automatic invalid_return(input int kind);
    rst_n=0; edge_tick(); set_profile(0);
    request='0; identity='0; return_bus='0; request.tag=7; request.generation=1;
    request.byte_enable='1; identity.tag=7; identity.generation=1;
    rst_n=1; launch_valid=1; edge_tick(); launch_valid=0;
    if (kind==0) begin return_bus.valid[0][0]=1; edge_tick(); end
    else if (kind==1) begin return_bus.valid[1][0]=1; edge_tick(); end
    else repeat (40) edge_tick();
    assert(fault && !done_valid) else $fatal(1,"invalid/missing read not detected kind=%0d",kind);
    abort_request=1; return_bus='0; edge_tick(); abort_request=0;
    assert(!fault && drained && !done_valid) else $fatal(1,"destructive data abort");
  endtask
  task automatic interleaved_reads;
    logic [511:0] multi_data[2];
    logic [1:0] seen;
    int fd, count, cycle_value, completions;
    logic [127:0] data_value;
    logic [7:0] valid_value;
    `include "multi.svh"
    rst_n=0; edge_tick(); set_profile(0);
    request='0; identity='0; return_bus='0;
    request.is_64_bytes=1; request.tag=1; request.generation=1;
    identity.tag=1; identity.generation=1; identity.config_epoch=7; identity.boot_epoch=3;
    rst_n=1; launch_valid=1; edge_tick(); launch_valid=0; done_ready=1;
    seen=0; completions=0;
    fd=$fopen({vector_directory,"/multi.txt"},"r");
    assert(fd!=0) else $fatal(1,"multi vectors");
    while (!$feof(fd)) begin
      count=$fscanf(fd,"%h %h %h\n",cycle_value,data_value,valid_value);
      assert(count==3) else $fatal(1,"multi parse");
      launch_valid=cycle_value==2; request.tag=2; identity.tag=2;
      return_bus.data=data_value; return_bus.valid=valid_value;
      #1;
      if (done_valid) begin
        assert(done.identity.tag inside {1,2}) else $fatal(1,"multi tag");
        assert(!seen[int'(done.identity.tag)-1] && !done.failed) else $fatal(1,"multi duplicate/failure");
        assert(done.data==multi_data[int'(done.identity.tag)-1]) else $fatal(1,"interleaved BL32 identity");
        seen[int'(done.identity.tag)-1]=1; completions++;
      end
      edge_tick(); assert(!fault) else $fatal(1,"multi fault CK=%0d",cycle_value);
    end
    $fclose(fd); launch_valid=0; done_ready=0;
    assert(completions==2 && drained) else $fatal(1,"multi completion");
  endtask
  task automatic completion_priority_stall;
    int fd;
    logic seen_first;
    lpddr5_mc_channel_completion_t held;
    rst_n=0; edge_tick(); set_profile(0);
    request='0; identity='0; return_bus='0;
    request.tag=1; identity.tag=1; request.rank_index=1; request.is_write=1; request.is_64_bytes=1;
    request.generation=1; identity.generation=1; request.byte_enable='1;
    rst_n=1; launch_valid=1; done_ready=0; edge_tick(); launch_valid=0;
    edge_tick(); request.is_write=0; request.is_64_bytes=0; request.tag=2; identity.tag=2;
    launch_valid=1; edge_tick(); launch_valid=0;
    seen_first=0; held='0;
    fd=$fopen({vector_directory,"/data-0.txt"},"r");
    assert(fd!=0) else $fatal(1,"priority vector open");
    while (!$feof(fd)) begin
      read_count=$fscanf(fd,"%h %h %h %h %h %h %h %h %h %h %h %h %h\n",cycle_number,
        ret_data,ret_valid,ret_dnv,ret_parity,wr_data,wr_mask,wr_enable,wr_cs,wr_parity,rd_enable,rd_cs,expected_done);
      assert(read_count==13) else $fatal(1,"priority vector parse");
      return_bus.data=ret_data; return_bus.valid=ret_valid; return_bus.dnv=ret_dnv; return_bus.link_parity=ret_parity;

      #1;
      if (done_valid) begin
        if (!seen_first) begin
          held=done; seen_first=1;
          assert(done.identity.tag==2) else $fatal(1,"later read should finish before delayed write flush");
        end else assert(done==held) else $fatal(1,"lower-index late completion displaced held response");
      end
      edge_tick(); assert(!fault) else $fatal(1,"priority test fault");
    end
    $fclose(fd); launch_valid=0; return_bus='0;
    repeat(12) begin
      #1; assert(done_valid && done==held) else $fatal(1,"late write displaced stalled read"); edge_tick();
    end
    assert(seen_first && done_valid && done.identity.tag==2) else $fatal(1,"priority stall not exercised");
    done_ready=1; edge_tick();
    assert(done_valid && done.identity.tag==1) else $fatal(1,"retained earlier read missing");
    edge_tick(); done_ready=0;
    assert(drained && !done_valid) else $fatal(1,"priority drain");
  endtask
  initial begin
    int cases_fd, case_count, case_number, profile_number, write_value, beats_value, rank_value;
    logic [511:0] data_value;
    logic [63:0] enables_value;
    logic checked_link_diagnostics;
    profile='0; request='0; identity='0; return_bus='0;
    if (!$value$plusargs("vectors=%s",vector_directory)) $fatal(1,"missing vectors");
    cases_fd=$fopen({vector_directory,"/cases.txt"},"r");
    assert(cases_fd!=0) else $fatal(1,"cases open");
    while (!$feof(cases_fd)) begin
      case_count=$fscanf(cases_fd,"%h %h %h %h %h %h %h\n",case_number,profile_number,write_value,beats_value,rank_value,data_value,enables_value);
      assert(case_count==7) else $fatal(1,"cases parse");
      run_case(case_number,profile_number,write_value,beats_value,rank_value,data_value,enables_value);
      if(!checked_link_diagnostics && profile.read_link && write_value==0) begin
        for(int errors=1;errors<=2;errors++) begin
          inject_link=errors;run_case(case_number,profile_number,write_value,beats_value,rank_value,data_value,enables_value);
        end
        inject_link=0;checked_link_diagnostics=1;
      end
    end
    $fclose(cases_fd);assert(checked_link_diagnostics) else $fatal(1,"missing read-link error cases");
    interleaved_reads();
    completion_priority_stall();
    inject_dnv=1;
    run_case(0,0,0,16,1,512'h00000000000000000000000000000000000000000000000000000000000000007e59340feac5a07b56310ce7c29d78532e09e4bf9a75502b06e1bc97724d2803,64'hffffffff);
    inject_dnv=0;
    invalid_return(0); invalid_return(1); invalid_return(2);
    $display("PASS data: 128 reference-derived transfers, 32 profiles, split/slice skew, stable completion");
    $finish;
  end
endmodule
