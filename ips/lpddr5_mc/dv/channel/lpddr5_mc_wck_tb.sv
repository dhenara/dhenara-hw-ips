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
    $display("PASS WCK: 128 independent half-WCK preamble/toggle/postamble/disable traces");
    $finish;
  end
endmodule
