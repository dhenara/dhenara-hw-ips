// Static admission checks deliberately keep the core in reset. Traffic and
// address/error settlement are exercised by the separate connected fixtures.
/* verilator lint_off PINCONNECTEMPTY */
module parameter_tb #(
  parameter int PORTS=1, CHANNELS=1, RANKS=1, DATA_BITS=64,
  parameter int ID_BITS=4, ADDRESS_BITS=32, DEPTH=16,
  parameter bit CHECK_CONTRACT=0
);
  import lpddr5_mc_contract_pkg::*;
  lpddr5_mc_host_core #(.PORTS(PORTS),.CHANNELS(CHANNELS),.RANKS(RANKS),
    .DATA_BITS(DATA_BITS),.ID_BITS(ID_BITS),.ADDRESS_BITS(ADDRESS_BITS),.DEPTH(DEPTH)) dut (
    .clk_i(1'b0),.rst_ni(1'b0),.enable_i(1'b0),.fatal_i(1'b0),.recover_i(1'b0),
    .cfg_valid_i(1'b0),.cfg_ready_o(),.cfg_i('0),
    .config_valid_o(),.fault_o(),.host_settled_o(),.late_error_o(),.stale_completions_o(),
    .aw_valid_i('0),.ar_valid_i('0),.w_valid_i('0),.aw_ready_o(),.ar_ready_o(),.w_ready_o(),
    .aw_i('0),.ar_i('0),.w_i('0),.b_valid_o(),.r_valid_o(),.b_ready_i('0),.r_ready_i('0),
    .b_o(),.r_o(),.b_slot_o(),.r_slot_o(),.b_generation_o(),.r_generation_o(),
    .response_event_valid_i('0),.response_event_ready_o(),.response_event_i('0),
    .core_slot_available_o(),.retire_valid_o(),.retire_ready_i('0),.retire_o(),
    .request_valid_o(),.request_ready_i('0),.request_o(),
    .request_boot_epoch_o(),.request_config_epoch_o(),
    .completion_valid_i('0),.completion_ready_o(),.completion_i('0),
    .feedback_valid_o(),.feedback_ready_i('0),.feedback_o()
  );

  task automatic contract_matrix;
    lpddr5_mc_parameters_t p, base;
    int widths[3]='{64,128,256};
    int depths[3]='{16,32,64};
    int divs[3]='{1,2,4};
    int accepted=0, rejected=0, boundaries=0;
    base='{ports:1,channels:1,ranks:1,axi_bits:64,id_bits:4,address_bits:32,
      queue_depth:16,service_div:1,wck_ratio:2,read_link:0,write_link:0,raw_parity:0};
    for(int ports=1;ports<=2;ports++) for(int channels=1;channels<=2;channels++)
      for(int ranks=1;ranks<=2;ranks++) foreach(widths[w])
        for(int ids=4;ids<=16;ids++) for(int addresses=32;addresses<=40;addresses++)
          foreach(depths[d]) foreach(divs[s]) for(int ratio=2;ratio<=4;ratio+=2)
            for(int links=0;links<8;links++) begin
              p=base;
              p.ports=3'(ports);p.channels=3'(channels);p.ranks=3'(ranks);
              p.axi_bits=9'(widths[w]);p.id_bits=5'(ids);p.address_bits=6'(addresses);
              p.queue_depth=7'(depths[d]);p.service_div=3'(divs[s]);p.wck_ratio=3'(ratio);
              p.read_link=1'(links);p.write_link=1'(links>>1);p.raw_parity=1'(links>>2);
              // Link ECC is admitted only with ratio-four raw parity. Plain
              // operation admits either parity capability at both ratios.
              if(links==0 || links==4 || (ratio==4 && links>=4)) begin
                assert(lpddr5_mc_parameters_valid(p)) else $fatal(1,"supported descriptor rejected: %p",p);
                accepted++;
              end else begin
                assert(!lpddr5_mc_parameters_valid(p)) else $fatal(1,"unsupported link descriptor admitted: %p",p);
                rejected++;
              end
            end
    // Change one field at a time from an otherwise valid image. Both numeric
    // limits and holes in enumerated sets must reject, without truncation.
    for(int field=0;field<10;field++) for(int choice=0;choice<3;choice++) begin
      p=base;
      case(field)
        0:p.ports=choice==0 ? 0 : choice==1 ? 3 : 7;
        1:p.channels=choice==0 ? 0 : choice==1 ? 3 : 7;
        2:p.ranks=choice==0 ? 0 : choice==1 ? 3 : 7;
        3:p.axi_bits=choice==0 ? 0 : choice==1 ? 32 : 192;
        4:p.id_bits=choice==0 ? 0 : choice==1 ? 3 : 17;
        5:p.address_bits=choice==0 ? 0 : choice==1 ? 31 : 41;
        6:p.queue_depth=choice==0 ? 0 : choice==1 ? 8 : 65;
        7:p.service_div=choice==0 ? 0 : choice==1 ? 3 : 7;
        8:p.wck_ratio=choice==0 ? 0 : choice==1 ? 3 : 7;
        9:p.queue_depth=choice==0 ? 15 : choice==1 ? 24 : 63;
      endcase
      assert(!lpddr5_mc_parameters_valid(p)) else $fatal(1,"invalid field admitted: %p",p);
      boundaries++;
    end
    assert(accepted==176904 && rejected==227448 && boundaries==30)
      else $fatal(1,"incomplete contract matrix %0d %0d %0d",accepted,rejected,boundaries);
    $display("PARAMETER_CONTRACT_PASS accepted=%0d rejected=%0d boundaries=%0d",accepted,rejected,boundaries);
  endtask

  initial begin
    if(CHECK_CONTRACT) contract_matrix();
    #1;
    $display("PARAMETER_CORE_ADMITTED");
    $finish;
  end
endmodule
