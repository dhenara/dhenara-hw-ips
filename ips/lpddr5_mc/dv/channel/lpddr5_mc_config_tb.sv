module lpddr5_mc_config_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0, rst_n=0, destructive_reset=0, drained=1, discard=0;
  logic [31:0] boot_epoch=0;
  lpddr5_mc_channel_config_t cfg, active, unused_prepared_config;
  lpddr5_mc_profile_t profile;
  lpddr5_mc_config_mailbox_t config_mailbox;
  logic prepared, prepare_error, active_valid, fault;
  logic [31:0] prepared_epoch, committed_epoch;
  lpddr5_mc_channel_config dut(.clk_ck_i(clk),.rst_ni(rst_n),.destructive_reset_i(destructive_reset),
    .discard_prepared_i(discard),.prepared_o(unused_prepared_config),
    .boot_epoch_i(boot_epoch),.drained_i(drained),.config_i(cfg),.mailbox_i(config_mailbox),
    .prepared_valid_o(prepared),.prepare_error_o(prepare_error),.prepared_epoch_o(prepared_epoch),
    .committed_epoch_o(committed_epoch),.active_valid_o(active_valid),.fault_o(fault),.active_o(active));
  `include "profiles.svh"
  task automatic tick;
    #1; clk=1; #1; clk=0; #1;
  endtask
  task automatic reset;
    rst_n=0; tick(); rst_n=1; config_mailbox='0; cfg='0; boot_epoch=0; destructive_reset=0; drained=1;discard=0;
  endtask
  task automatic load_config(input int index_value, sequence_value);
    set_profile(index_value); cfg='0; cfg.profile=profile; cfg.service_div=2;
    cfg.age_tick_div=8; cfg.urgent_age=32; cfg.bw_window=3000;
    cfg.boot_epoch=boot_epoch; cfg.config_epoch=32'(sequence_value);
    config_mailbox='0; config_mailbox.destinations=1; config_mailbox.epoch=32'(sequence_value); config_mailbox.payload_crc=config_crc(cfg);
  endtask
  initial begin
    config_mailbox='0; cfg='0; profile='0;
    for (int p=0;p<32;p++) begin
      reset(); load_config(p,0);
      assert(profile_valid(profile) && config_valid(cfg)) else $fatal(1,"valid fixture %0d",p);
      config_mailbox.prepare=1; tick();
      assert(prepared && prepared_epoch==0 && !active_valid && !fault) else $fatal(1,"prepare");
      tick(); assert(prepared && !fault) else $fatal(1,"idempotent prepare");
      config_mailbox.prepare=0; config_mailbox.commit=1; tick();
      assert(active_valid && active==cfg && committed_epoch==0 && !fault) else $fatal(1,"commit");
      tick(); assert(active_valid && !fault) else $fatal(1,"idempotent commit");
      config_mailbox='0; destructive_reset=1; tick(); destructive_reset=0; boot_epoch=1;
      assert(!active_valid && !prepared) else $fatal(1,"reset invalidation");
      load_config(p,0); config_mailbox.prepare=1; tick();
      assert(prepare_error && fault && !active_valid) else $fatal(1,"retired epoch accepted");
      reset(); load_config(p,7); cfg.profile.dfi_wck_tphy_wrcsgap=1; config_mailbox.payload_crc=config_crc(cfg);
      config_mailbox.prepare=1; tick(); assert(prepare_error && fault && !prepared) else $fatal(1,"nonzero CS gap");
      reset(); load_config(p,7); config_mailbox.payload_crc^=1; config_mailbox.prepare=1; tick();
      assert(prepare_error && fault && !prepared) else $fatal(1,"CRC mismatch");
      reset(); load_config(p,7); config_mailbox.prepare=1; tick(); config_mailbox.prepare=0;
      cfg.service_div=4; config_mailbox.commit=1; tick();
      assert(prepare_error && fault && !active_valid) else $fatal(1,"mutable snapshot");
      reset(); load_config(p,7); config_mailbox.prepare=1; drained=0; tick();
      assert(prepare_error && fault && !active_valid) else $fatal(1,"undrained prepare");
      reset(); load_config(p,7); config_mailbox.commit=1; tick();
      assert(prepare_error && fault && !active_valid) else $fatal(1,"commit without prepare");
      reset(); load_config(p,7); config_mailbox.prepare=1; tick(); config_mailbox.prepare=0; config_mailbox.epoch=8; config_mailbox.commit=1; tick();
      assert(prepare_error && fault && !active_valid) else $fatal(1,"stale partial commit");
      reset(); load_config(p,7); cfg.boot_epoch=1; config_mailbox.payload_crc=config_crc(cfg); config_mailbox.prepare=1; tick();
      assert(prepare_error && fault && !active_valid) else $fatal(1,"wrong boot epoch");
      reset();load_config(p,7);config_mailbox.prepare=1;tick();config_mailbox.prepare=0;config_mailbox.commit=1;tick();
      load_config(p,8);config_mailbox.prepare=1;tick();config_mailbox='0;discard=1;tick();discard=0;
      assert(active_valid && committed_epoch==7 && !prepared && !fault) else $fatal(1,"decline changed active image");
      load_config(p,7);config_mailbox.commit=1;tick();assert(!fault && active_valid) else $fatal(1,"active commit lost idempotence after decline");
      config_mailbox='0;destructive_reset=1;tick();destructive_reset=0;boot_epoch=1;
      load_config(p,8);config_mailbox.prepare=1;tick();
      assert(prepare_error && fault && !prepared) else $fatal(1,"recovery lowered declined-sequence fence");
    end
    // Keep unsupported capabilities/profile boundaries closed even with a
    // correct mailbox CRC; transport integrity does not qualify a profile.
    for(int bad=0;bad<12;bad++) begin
      reset();load_config(0,9);
      case(bad)
        0: cfg.profile.ranks=0;
        1: cfg.profile.ranks=3;
        2: cfg.profile.ratio=3;
        3: begin cfg.profile.write_link=1;cfg.profile.raw_parity=0;end
        4: begin cfg.profile.read_link=1;cfg.profile.read_dbi=1;end
        5: begin cfg.profile.write_link=1;cfg.profile.device_write_link=0;end
        6: cfg.profile.mr[1]^=8'h01;
        7: cfg.profile.dfi_wck_tphy_rdcsgap=1;
        8: cfg.profile.dfi_wck_tphy_rdlat=0;
        9: cfg.service_div=3;
        10: cfg.age_tick_div=0;
        11: cfg.bw_window=0;
        default: $fatal(1,"test mutation");
      endcase
      config_mailbox.payload_crc=config_crc(cfg);config_mailbox.prepare=1;tick();
      assert(prepare_error && fault && !prepared && !active_valid) else $fatal(1,"unsupported snapshot admitted case=%0d",bad);
    end
    $display("PASS configuration: 32 snapshots, immutable publication, CRC/epoch/drain/CS-gap failures and unsupported profile/capability boundaries");
    $finish;
  end
endmodule
