module apb_cdc_tb;
  import lpddr5_mc_apb_metadata_pkg::*;
  import lpddr5_mc_apb_pkg::*;
  logic aclk = 0, cclk = 0;
  always #5 aclk <= ~aclk;
  always begin
    #2;
    forever #3.5 cclk <= ~cclk;
  end
  logic arst_n = 0, crst_n = 0;
  logic a_rearm = 0, c_rearm = 0;
  logic a_rearm_ready, c_rearm_ready;
  logic a_enable = 1, c_enable = 1, a_fault, c_fault;
  logic a_config_valid = 0, a_config_ready, c_config_valid, c_config_ready = 0;
  lpddr5_mc_configuration_payload_t a_config, c_config, expected_config;
  logic c_ack_valid = 0, c_ack_ready, a_ack_valid, a_ack_ready = 0;
  lpddr5_mc_publication_ack_t c_ack = '0, a_ack;
  logic c_status_valid = 0, c_status_ready, a_status_valid, a_status_ready = 0;
  lpddr5_mc_channel_status_t c_status, a_status, expected_status;
  int checks = 0;

  lpddr5_mc_control_link dut (
    .aclk_i(aclk), .arst_ni(arst_n), .a_enable_i(a_enable), .a_rearm_i(a_rearm), .a_fault_o(a_fault),
    .a_rearm_ready_o(a_rearm_ready), .c_rearm_ready_o(c_rearm_ready),
    .cclk_i(cclk), .crst_ni(crst_n), .c_enable_i(c_enable), .c_rearm_i(c_rearm), .c_fault_o(c_fault),
    .a_config_valid_i(a_config_valid), .a_config_ready_o(a_config_ready), .a_config_i(a_config),
    .c_config_valid_o(c_config_valid), .c_config_ready_i(c_config_ready), .c_config_o(c_config),
    .c_ack_valid_i(c_ack_valid), .c_ack_ready_o(c_ack_ready), .c_ack_i(c_ack),
    .a_ack_valid_o(a_ack_valid), .a_ack_ready_i(a_ack_ready), .a_ack_o(a_ack),
    .c_status_valid_i(c_status_valid), .c_status_ready_o(c_status_ready), .c_status_i(c_status),
    .a_status_valid_o(a_status_valid), .a_status_ready_i(a_status_ready), .a_status_o(a_status)
  );

  task automatic check(input logic condition, input string explanation);
    assert(condition) else $fatal(1, "%s", explanation);
    checks++;
  endtask
  task automatic rearm();
    do @(negedge aclk); while(!a_rearm_ready);
    a_rearm = 1;
    @(negedge aclk); a_rearm = 0;
    do @(negedge cclk); while(!c_rearm_ready);
    c_rearm = 1;
    @(negedge cclk); c_rearm = 0;
    wait(!a_fault && !c_fault && a_config_ready && c_ack_ready && c_status_ready);
  endtask

  initial begin
    a_config.publication = '{epoch: 32'h12345678, payload_crc: 32'h89abcdef,
        prepare: 1'b1, commit: 1'b0, abort_request: 1'b0, destinations: 2'b11};
    a_config.boot_epoch = 32'habcdef01;
    c_status.events = 13'h1020;
    c_status.error_valid = 1;
    c_status.perf_capture = 1;
    for (int r = 0; r < REG_COUNT; r++) begin
      a_config.image[r] = 32'h13579bdf ^ 32'(r);
      c_status.image[r] = 32'h2468ace0 ^ (32'(r) << 8);
    end
    expected_config = a_config;
    expected_status = c_status;
    repeat(3) @(negedge aclk);
    arst_n = 1;
    @(negedge cclk); crst_n = 1;
    repeat(5) @(negedge aclk);
    check(a_fault && c_fault && !a_config_ready, "reset release bypassed recovery fence");
    rearm();
    @(negedge aclk); a_config_valid = 1;
    @(posedge aclk);
    check(a_config_ready, "source configuration handshake");
    @(negedge aclk); a_config_valid = 0;
    for (int r = 0; r < REG_COUNT; r++) a_config.image[r] = ~a_config.image[r];
    wait(c_config_valid);
    #1;
    check(c_config == expected_config, "torn large configuration payload");
    @(posedge cclk);
    #1;
    c_enable = 0;
    repeat(5) @(negedge cclk);
    check(c_config_valid && c_config == expected_config, "stalled exposure changed on disable");
    c_enable = 1;
    c_config_ready = 1;
    @(negedge cclk); c_config_ready = 0;
    repeat(4) @(negedge aclk);
    check(!c_config_valid && a_config_ready, "configuration acknowledgement lifetime");
    c_ack = '{sequence_number: expected_config.publication.epoch,
        boot_epoch: expected_config.boot_epoch, phase: LPDDR5_MC_CONFIG_PREPARE, accepted: 1'b1};
    @(negedge cclk); c_ack_valid = 1;
    @(posedge cclk); check(c_ack_ready, "channel ack handshake");
    @(negedge cclk); c_ack_valid = 0;
    wait(a_ack_valid);
    #1;
    check(a_ack == c_ack, "publication ack crossed incoherently");
    repeat(3) @(negedge aclk);
    check(a_ack_valid && a_ack == c_ack, "ack did not remain stable under backpressure");
    a_ack_ready = 1;
    @(negedge aclk); a_ack_ready = 0;
    @(negedge cclk); c_status_valid = 1;
    @(posedge cclk); check(c_status_ready, "channel status handshake");
    @(negedge cclk); c_status_valid = 0;
    for (int r = 0; r < REG_COUNT; r++) c_status.image[r] = 0;
    wait(a_status_valid);
    #1;
    check(a_status == expected_status, "torn coherent status image");
    repeat(4) @(negedge aclk);
    check(a_status_valid && a_status == expected_status, "status lifetime before consumption");
    a_status_ready = 1;
    @(negedge aclk); a_status_ready = 0;

    // Endpoint reset fences both directions and cannot replay an old epoch.
    wait(a_config_ready);
    a_config.boot_epoch = expected_config.boot_epoch + 1;
    @(negedge aclk); a_config_valid = 1;
    @(posedge aclk); check(a_config_ready, "second configuration admission");
    @(negedge aclk); a_config_valid = 0;
    #1; crst_n = 0;
    #1;
    check(a_fault && c_fault && !a_config_ready && !c_config_valid, "endpoint reset did not abort transport");
    @(negedge cclk); crst_n = 1;
    repeat(7) @(negedge aclk);
    check(a_fault && c_fault && !a_config_ready, "reset resumed without explicit rearm");
    // This testbench is the owner: the old fixture is explicitly discarded,
    // and the next submission carries a fresh boot/configuration identity.
    a_config.publication.epoch++;
    a_config.boot_epoch++;
    expected_config = a_config;
    rearm();
    @(negedge aclk); a_config_valid = 1;
    @(posedge aclk); check(a_config_ready, "fresh configuration admission");
    @(negedge aclk); a_config_valid = 0;
    wait(c_config_valid);
    #1;
    check(c_config == expected_config, "stale configuration replayed after reset");
    c_config_ready = 1;
    @(negedge cclk); c_config_ready = 0;
    $display("APB_CDC_PASS checks=%0d clocks=10ns/7ns phase=2ns words=%0d", checks, REG_COUNT);
    $finish;
  end
  initial begin
    #20000;
    $fatal(1, "control CDC test deadline");
  end
endmodule
