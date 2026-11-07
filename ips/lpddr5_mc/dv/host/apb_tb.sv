module apb_tb;
  timeunit 1ns;
  timeprecision 1ps;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_apb_metadata_pkg::*;
  import lpddr5_mc_apb_pkg::*;
  logic pclk = 0;
  always #5 pclk <= ~pclk;
  logic preset_n = 0;
  logic psel = 0, penable = 0, pready;
  lpddr5_mc_apb_request_t req = '0;
  lpddr5_mc_apb_response_t rsp;
  logic enabled = 0, quiesced = 1, settled = 1;
  logic [1:0] active_fsp = 0;
  logic link_fault = 0, recovery = 0, global_valid = 0, global_ready;
  lpddr5_mc_register_image_t global_status;
  logic [1:0] status_valid = 0, status_ready;
  lpddr5_mc_channel_status_t [1:0] status;
  logic [1:0] cfg_valid, cfg_ready = '1;
  lpddr5_mc_config_mailbox_t [1:0] cfg;
  lpddr5_mc_register_image_t [1:0] cfg_image, active_image;
  logic [31:0] cfg_boot, epoch;
  logic [1:0] ack_valid = 0, ack_ready;
  lpddr5_mc_publication_ack_t [1:0] ack = '0;
  logic cmd_valid, cmd_ready = 1;
  lpddr5_mc_control_command_t cmd;
  logic [2:0] protection;
  logic irq, cfg_good, fault, busy;
  int command_count = 0;
  logic [31:0] last_command_sequence = 0;
  logic [31:0] read_data;
  logic [31:0] held_sequence;
  logic [31:0] held_crc;
  lpddr5_mc_register_image_t held_image;
  int checks = 0;

  lpddr5_mc_apb #(.PUBLICATION_TIMEOUT(900)) dut (
    .pclk, .preset_n, .psel_i(psel), .penable_i(penable), .request_i(req),
    .pready_o(pready), .response_o(rsp), .enabled_i(enabled), .quiesced_i(quiesced),
    .host_settled_i(settled), .active_fsp_i(active_fsp), .link_fault_i(link_fault),
    .recovery_confirmed_i(recovery), .global_status_valid_i(global_valid),
    .global_status_i(global_status), .global_status_ready_o(global_ready),
    .status_valid_i(status_valid), .status_i(status), .status_ready_o(status_ready),
    .config_valid_o(cfg_valid), .config_ready_i(cfg_ready), .config_o(cfg),
    .config_image_o(cfg_image), .config_boot_epoch_o(cfg_boot),
    .config_ack_valid_i(ack_valid), .config_ack_i(ack), .config_ack_ready_o(ack_ready),
    .command_valid_o(cmd_valid), .command_ready_i(cmd_ready), .command_o(cmd),
    .last_protection_o(protection), .irq_o(irq), .configuration_valid_o(cfg_good),
    .fault_o(fault), .publication_busy_o(busy), .configuration_epoch_o(epoch),
    .active_image_o(active_image)
  );

  task automatic check(input logic condition, input string explanation);
    assert(condition) else $fatal(1, "%s", explanation);
    checks++;
  endtask

  task automatic access(input logic write, input logic [17:0] address,
      input logic [31:0] data, input logic [3:0] strobes, input logic expect_error,
      output logic [31:0] result);
    @(negedge pclk);
    psel = 1;
    penable = 0;
    req = '{address: address, write: write, data: data, strobe: strobes, protection: 3'b101};
    @(negedge pclk);
    penable = 1;
    #1;
    check(pready, "APB access did not complete");
    check(rsp.error == expect_error, $sformatf("APB error mismatch at %h", address));
    result = rsp.data;
    @(negedge pclk);
    penable = 0;
    psel = 0;
  endtask

  task automatic write_reg(input logic [17:0] address, input logic [31:0] data,
      input logic [3:0] strobe = 15, input logic error = 0);
    logic [31:0] unused_read_data;
    access(1, address, data, strobe, error, unused_read_data);
  endtask

  task automatic read_reg(input logic [17:0] address, output logic [31:0] data);
    access(0, address, 0, 0, 0, data);
  endtask

  task automatic send_ack(input logic [1:0] channels, input lpddr5_mc_publication_phase_t phase,
      input logic [31:0] sequence_number, input logic accepted = 1);
    @(negedge pclk);
    for (int c = 0; c < 2; c++)
      ack[c] = '{sequence_number: sequence_number, boot_epoch: cfg_boot, phase: phase, accepted: accepted};
    ack_valid = channels;
    @(negedge pclk);
    ack_valid = 0;
  endtask

  task automatic publish(input logic [31:0] boot_epoch = 0);
    write_reg(18'h20, 1);
    wait(cfg[0].prepare);
    #1;
    check(cfg[1].epoch == cfg[0].epoch && cfg[1].prepare && cfg[1].destinations == cfg[0].destinations &&
        cfg_valid == 2'b11, "prepare fanout");
    held_sequence = cfg[0].epoch;
    held_crc = cfg[0].payload_crc;
    held_image = cfg_image[0];
    check(cfg_boot == boot_epoch && !cfg_good, "prepare epoch/admission fence");
    repeat(4) @(negedge pclk);
    write_reg(18'h10000, 32'h7777, 15, 1);
    check(cfg_image[0] == held_image && cfg[0].payload_crc == held_crc, "frozen snapshot changed");
    send_ack(2'b01, LPDDR5_MC_CONFIG_PREPARE, held_sequence);
    check(!cfg_good && !cfg[0].commit, "partial prepare committed");
    send_ack(2'b10, LPDDR5_MC_CONFIG_PREPARE, held_sequence);
    wait(cfg[0].commit);
    repeat(2) @(negedge pclk);
    send_ack(2'b01, LPDDR5_MC_CONFIG_COMMIT, held_sequence);
    check(!cfg_good, "partial commit enabled admission");
    send_ack(2'b10, LPDDR5_MC_CONFIG_COMMIT, held_sequence);
    #1;
    check(cfg_good && !fault && !busy && epoch == held_sequence, "all-channel publication failed");
    check(active_image[0] == held_image && active_image[1] == cfg_image[1], "published image differs");
  endtask

  // Checks command delivery at its actual mailbox handshake, separately from
  // command completion or channel ownership changes.
  always @(posedge pclk) begin
    if (cmd_valid && cmd_ready) begin
      assert(cmd.sequence_number > last_command_sequence) else $fatal(1, "duplicate/stale command sequence");
      if (cmd.register_index == 10'(INDEX_TRAIN_ACK)) begin
        assert(cmd.boot_epoch == 7 && cmd.destinations == 0 && cmd.command_mask == 1 &&
            cmd.write_data == 1 && cmd.training_token == 7) else $fatal(1, "training command payload");
      end
      last_command_sequence <= cmd.sequence_number;
      command_count <= command_count + 1;
    end
  end

  initial begin
    for (int r = 0; r < REG_COUNT; r++) begin
      global_status[r] = 0;
      for (int c = 0; c < 2; c++) status[c].image[r] = 0;
    end
    for (int c = 0; c < 2; c++) begin
      status[c].events = 0;
      status[c].error_valid = 0;
      status[c].perf_capture = 0;
    end
    repeat(3) @(negedge pclk);
    preset_n = 1;
    check(global_ready && &status_ready && &ack_ready, "stream readiness");
    read_reg(0, read_data);
    check(read_data == 32'h4c500200, "ID value");
    check(protection == 3'b101, "PPROT diagnostic retention");
    write_reg(0, 1, 15, 1);
    write_reg(18'h1, 1, 15, 1);
    write_reg(18'h3fffc, 1, 15, 1);
    access(0, 0, 0, 1, 1, read_data);
    write_reg(18'h1c, 32'hffffffff, 1);
    read_reg(18'h1c, read_data);
    check(read_data == 255, "PSTRB and reserved-bit masking");
    write_reg(18'h1c, 32'hffffffff);
    read_reg(18'h1c, read_data);
    check(read_data == 32'h1fff, "IRQ_MASK reserved bits13..31");
    write_reg(18'h18, 32'hffffffff);
    read_reg(18'h18, read_data);
    check(read_data == 0, "IRQ_STATUS reserved bits13..31");
    write_reg(18'h1c, 0);
    write_reg(18'h8, 32'h10);
    write_reg(18'h10000, 32'h1234);
    read_reg(18'h20000, read_data);
    check(read_data == 32'h1234, "channel-zero broadcast staging");
    write_reg(18'h20000, 1, 15, 1);
    write_reg(18'h8, 0);
    enabled = 1;
    quiesced = 0;
    write_reg(18'h28, 32'h101, 15, 1);
    write_reg(18'h10000, 1, 15, 1);
    write_reg(18'h11000, 1, 15, 1);
    write_reg(18'h11400, 1); // inactive FSP remains writable
    enabled = 0;
    quiesced = 1;
    write_reg(18'h34, 1, 15, 1); // TRAIN_ACK cannot manufacture TRAIN_DONE
    global_status[INDEX_BOOT_EPOCH] = 7;
    global_status[INDEX_TRAIN_REQUEST] = 1;
    @(negedge pclk); global_valid = 1;
    @(negedge pclk); global_valid = 0;
    write_reg(18'h48, 6);
    write_reg(18'h34, 1, 15, 1);
    write_reg(18'h48, 7);
    write_reg(18'h34, 1);
    read_reg(18'h44, read_data);
    check(read_data == 0, "TRAIN_ACK wrote hardware TRAIN_DONE");
    repeat(2) @(negedge pclk);
    check(command_count == 1, "training command not delivered exactly once");
    force dut.sequence_q = 32'hfffffffe;
    write_reg(18'h8, 32'h13, 15, 1);
    read_reg(18'h8, read_data);
    check(read_data == 0, "sequence exhaustion caused a partial command/staging effect");
    force dut.sequence_q = 32'd1;
    @(negedge pclk);
    release dut.sequence_q;
    cmd_ready = 0;
    write_reg(18'h8, 15);
    write_reg(18'h8, 15);
    write_reg(18'h8, 17, 15, 1);
    read_reg(18'h8, read_data);
    check(read_data == 0, "mailbox exhaustion caused partial RW mutation");
    cmd_ready = 1;
    repeat(10) @(negedge pclk);
    check(command_count == 9, "command queue loss or duplicate");
    // Keep PENABLE high after completion: one transfer has one side effect.
    @(negedge pclk); psel = 1; penable = 0;
    req = '{address: 18'h8, write: 1'b1, data: 32'd1, strobe: 4'hf, protection: 3'b0};
    @(negedge pclk); penable = 1;
    repeat(4) @(negedge pclk);
    psel = 0; penable = 0;
    repeat(3) @(negedge pclk);
    check(command_count == 10, "held APB access repeated a command");
    write_reg(18'h30, 3);
    write_reg(18'h30, 32'h100, 2, 1);
    write_reg(18'h30, 2);
    cmd_ready = 0;
    write_reg(18'h30, 32'h100, 2);
    check(cmd_valid && cmd.register_index == 10'(INDEX_DVFS) && cmd.write_data == 32'h102,
        "DVFS command lost unstroked staging FSP payload");
    cmd_ready = 1;
    repeat(3) @(negedge pclk);

    // Independent error events keep the first detail and keep counting.
    status[0].image[INDEX_BOOT_EPOCH] = 7;
    status[0].events = 13'h20;
    status[0].error_valid = 1;
    status[0].image[INDEX_ERROR_ADDR_LO] = 32'h12345678;
    status[0].image[INDEX_ERROR_ADDR_HI] = 32'hff;
    status[0].image[INDEX_ERROR_AXI_ID] = 32'hfedc;
    status[0].image[INDEX_ERROR_TAG_LO] = 32'h89abcdef;
    status[0].image[INDEX_ERROR_TAG_HI] = 32'h12345678;
    status[0].image[INDEX_ERROR_INFO] = 32'hffffffff;
    @(negedge pclk); status_valid = 1;
    @(negedge pclk); status_valid = 0;
    status[0].image[INDEX_ERROR_ADDR_LO] = 32'hbad;
    @(negedge pclk); status_valid = 1;
    @(negedge pclk); status_valid = 0;
    read_reg(18'h10240, read_data);
    check(read_data == 32'h12345678, "first error overwritten");
    read_reg(18'h10258, read_data);
    check(read_data == 2, "CE count did not continue");
    status[0].image[INDEX_BOOT_EPOCH] = 6;
    @(negedge pclk); status_valid = 1;
    @(negedge pclk); status_valid = 0;
    read_reg(18'h10258, read_data);
    check(read_data == 2, "stale boot status changed counters");
    status[0].image[INDEX_BOOT_EPOCH] = 7;
    read_reg(18'h10254, read_data);
    check(read_data == 32'h3ffff, "ERROR_INFO reserved bits");
    read_reg(18'h18, read_data);
    check(read_data[11] && read_data[5], "error/overflow events missing");
    write_reg(18'h1c, 32'h20);
    check(irq, "IRQ mask gating");
    write_reg(18'h18, 32'h20);
    @(negedge pclk); status_valid = 1;
    @(negedge pclk); status_valid = 0;
    read_reg(18'h10240, read_data);
    check(read_data == 32'hbad, "W1C did not rearm first error");
    check(!cfg_good, "IRQ clear restored configuration trust");
    for (int r = 0; r < REG_COUNT; r++) status[0].image[r] = 0;
    status[0].events = 0;
    status[0].error_valid = 0;
    status[0].perf_capture = 0;
    status[0].image[INDEX_BOOT_EPOCH] = 7;
    status[0].perf_capture = 1;
    status[0].image[INDEX_PERF_CYCLES_0] = 32'hffffffff;
    status[0].image[INDEX_PERF_CYCLES_1] = 32'h12345678;
    @(negedge pclk); status_valid = 1;
    @(negedge pclk); status_valid = 0;
    status[0].perf_capture = 0;
    status[0].image[INDEX_PERF_CYCLES_0] = 0;
    status[0].image[INDEX_PERF_CYCLES_1] = 0;
    @(negedge pclk); status_valid = 1;
    @(negedge pclk); status_valid = 0;
    read_reg(18'h102c8, read_data);
    check(read_data == 32'hffffffff, "performance snapshot changed without capture");
    read_reg(18'h102cc, read_data);
    check(read_data == 32'h12345678, "performance high/low image torn");

    // The canonical explicit integration image, generated as test data only.
    `include "apb_fixture.svh"
    settled = 0;
    write_reg(18'h20, 1, 15, 1);
    settled = 1;
    publish(7);
    write_reg(18'h10008, 2); // bad AGE_STEP rejects before publication
    write_reg(18'h20, 1, 15, 1);
    check(cfg_good, "invalid staging changed live validity");
    write_reg(18'h10008, 1);
    write_reg(18'h20, 1);
    wait(cfg[0].prepare);
    repeat(2) @(negedge pclk);
    held_sequence = cfg[0].epoch;
    send_ack(2'b01, LPDDR5_MC_CONFIG_PREPARE, held_sequence - 1);
    #1;
    check(fault && !cfg_good, "stale prepare ack did not fault");
    wait(cfg[0].abort_request);
    repeat(2) @(negedge pclk);
    send_ack(2'b11, LPDDR5_MC_CONFIG_ABORT, held_sequence);
    check(!busy, "abort did not settle");
    write_reg(18'h18, 32'h1000);
    check(fault && !cfg_good, "IRQ clear recovered fault");
    @(negedge pclk); recovery = 1;
    @(negedge pclk); recovery = 0;
    check(!fault && !cfg_good, "confirmed destructive recovery state");
    publish(7);
    write_reg(18'h20, 1);
    wait(cfg[0].prepare);
    repeat(2) @(negedge pclk);
    held_sequence = cfg[0].epoch;
    send_ack(2'b01, LPDDR5_MC_CONFIG_PREPARE, held_sequence);
    wait(fault);
    check(!cfg_good, "partial prepare timeout enabled traffic");
    wait(cfg[0].abort_request);
    repeat(2) @(negedge pclk);
    send_ack(2'b11, LPDDR5_MC_CONFIG_ABORT, held_sequence);
    check(!busy, "timeout abort did not settle");
    @(negedge pclk); recovery = 1;
    @(negedge pclk); recovery = 0;
    held_sequence = epoch + 1;
    write_reg(18'h20, 1);
    send_ack(2'b01, LPDDR5_MC_CONFIG_PREPARE, held_sequence);
    check(fault && !cfg_good, "ack before a prepare request was ignored");
    wait(cfg[0].abort_request);
    repeat(2) @(negedge pclk);
    send_ack(2'b11, LPDDR5_MC_CONFIG_ABORT, held_sequence);
    check(!busy, "premature-ack abort did not settle");
    $display("APB_PASS checks=%0d commands=%0d", checks, command_count);
    $finish;
  end
  initial begin
    #2000000;
    $fatal(1, "APB test deadline");
  end
endmodule
