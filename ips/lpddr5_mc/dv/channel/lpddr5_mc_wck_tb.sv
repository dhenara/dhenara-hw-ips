module lpddr5_mc_wck_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0, rst_n=0, abort_request=0, sync=0, sync_write=0, sync_rank=0;
  logic access_valid=0, access_write=0, access_bl32=0, stop=0;
  logic disabled, reusable, fault, active_rank, active_write,stop_ready;
  lpddr5_mc_profile_t profile;
  lpddr5_mc_wck4_t bus_value, expected_bus;
  string vector_directory;
  logic unused_outputs;
  assign unused_outputs=^{reusable,active_rank,active_write,stop_ready};
  lpddr5_mc_wck dut (.clk_ck_i(clk),.rst_ni(rst_n),.abort_i(abort_request),.profile_i(profile),
    .sync_i(sync),.sync_write_i(sync_write),.sync_rank_i(sync_rank),
    .access_i(access_valid),.access_write_i(access_write),.access_bl32_i(access_bl32),.stop_i(stop),
    .stop_ready_o(stop_ready),.disabled_o(disabled),.reusable_o(reusable),.fault_o(fault),.active_rank_o(active_rank),.active_write_o(active_write),.wck_o(bus_value));
  `include "profiles.svh"
  task automatic edge_tick;
    #1; clk=1; #1; clk=0; #1;
  endtask
  task automatic run_case(input int number, profile_number, write_access, beats, rank_number,
      input logic [511:0] unused_data, input logic [63:0] unused_enables);
    int fd, count, cycle_number, stop_request;
    logic [7:0] en;
    logic [15:0] cs, toggle_value;
    assert(write_access inside {0,1}) else $fatal(1,"test direction");
    assert(rank_number inside {0,1}) else $fatal(1,"test rank");
    rst_n=0; edge_tick(); set_profile(profile_number);
    sync_write=1'(write_access); sync_rank=1'(rank_number); sync=1;
    access_write=1'(write_access); access_bl32=beats==32;
    rst_n=1; edge_tick(); sync=0;
    fd=$fopen($sformatf("%s/wck-%0d.txt",vector_directory,number),"r");
    assert(fd!=0) else $fatal(1,"WCK vectors");
    while (!$feof(fd)) begin
      count=$fscanf(fd,"%h %h %h %h %h\n",cycle_number,stop_request,en,cs,toggle_value);
      assert(stop_request inside {0,1}) else $fatal(1,"stop bit");
      assert(count==5) else $fatal(1,"parse WCK");
      access_valid=cycle_number==1; stop=1'(stop_request);
      expected_bus.enable=en; expected_bus.cs=cs; expected_bus.toggle=toggle_value;
      #1;
      assert(bus_value==expected_bus) else $fatal(1,"WCK case=%0d CK=%0d actual=%h expected=%h",number,cycle_number,bus_value,expected_bus);
      edge_tick();
      assert(!fault) else $fatal(1,"WCK fault case=%0d CK=%0d",number,cycle_number);
    end
    $fclose(fd); access_valid=0; stop=0;
    assert(disabled) else $fatal(1,"WCK did not disable");
  endtask
  // Prepared behavioral regression: exercise real sequential expiry, never
  // force an internal timer. stop_ready is the backend/register owner's seam.
  task automatic boundary_case(input int profile_number,delta,next_owner,input bit inject_illegal);
    logic [63:0] natural_end,target;
    bit expect_ready;
    rst_n=0;sync=0;access_valid=0;stop=0;edge_tick();set_profile(profile_number);
    sync_write=1;sync_rank=0;sync=1;rst_n=1;edge_tick();sync=0;
    access_write=1;access_bl32=1;access_valid=1;
    natural_end=dut.tick_q+64'(profile.wck_twck_dis);
    edge_tick();access_valid=0;
    if(profile.always_on) natural_end=dut.post_q+64'(3*int'(profile.ratio));
    target=64'(longint'(natural_end)+longint'(delta*int'(profile.ratio)));
    while(dut.tick_q<target) edge_tick();
    expect_ready=profile.always_on || delta<0;
    assert(stop_ready==expect_ready) else $fatal(1,"stop boundary profile=%0d delta=%0d ready=%0d",profile_number,delta,stop_ready);
    if(!expect_ready) assert(bus_value=='0) else $fatal(1,"expired rank still selected");
    stop=stop_ready || inject_illegal;edge_tick();stop=0;
    assert(fault==(inject_illegal && !expect_ready)) else $fatal(1,"stale stop fault contract");
    for(int n=0;n<200;n++) begin
      if(disabled) break;
      edge_tick();
    end
    assert(disabled && bus_value=='0) else $fatal(1,"natural/explicit disable did not finish");
    repeat(3) begin edge_tick();assert(disabled && bus_value=='0) else $fatal(1,"clock/rank resurrected");end
    if(!fault) begin
      // Next pending write, read or MRR (read BL16) gets a fresh rank1 sync.
      sync_rank=1;sync_write=next_owner==0;sync=1;edge_tick();sync=0;
      access_write=next_owner==0;access_bl32=next_owner!=2;access_valid=1;
      edge_tick();access_valid=0;
      assert(!fault && active_rank && active_write==(next_owner==0)) else $fatal(1,"fresh owner not admitted");
    end
  endtask
  initial begin
    int cases_fd, case_count, case_number, profile_number, write_value, beats_value, rank_value;
    logic [511:0] data_value;
    logic [63:0] enables_value;
    profile='0;
    if (!$value$plusargs("vectors=%s",vector_directory)) $fatal(1,"missing vectors");
    cases_fd=$fopen({vector_directory,"/cases.txt"},"r");
    assert(cases_fd!=0) else $fatal(1,"cases open");
    while (!$feof(cases_fd)) begin
      case_count=$fscanf(cases_fd,"%h %h %h %h %h %h %h\n",case_number,profile_number,write_value,beats_value,rank_value,data_value,enables_value);
      assert(case_count==7) else $fatal(1,"cases parse");
      run_case(case_number,profile_number,write_value,beats_value,rank_value,data_value,enables_value);
    end
    $fclose(cases_fd);
    for(int p=0;p<32;p++) for(int delta=-1;delta<=1;delta++) for(int owner=0;owner<3;owner++) begin
      boundary_case(p,delta,owner,0);
      if(!profile.always_on && delta>=0) boundary_case(p,delta,owner,1);
    end
    $display("WCK_BOUNDARY_PASS profiles=32 stop=before/at/after next=WR/RD/MRR stale_stop=rejected");
    $display("PASS WCK: 128 independent half-WCK preamble/toggle/postamble/disable traces");
    $finish;
  end
endmodule
