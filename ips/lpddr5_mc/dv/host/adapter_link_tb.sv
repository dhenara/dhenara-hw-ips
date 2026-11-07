module adapter_link_tb #(
  parameter int DEPTH = 16
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_host_pkg::*;
  localparam int COUNT = DEPTH * 3 + 7;
  typedef struct packed {
    logic [31:0] boot_epoch, config_epoch;
    lpddr5_mc_granule_t granule;
  } request_record_t;
  logic hclk = 0, cclk = 0;
  always #4.5 hclk <= ~hclk;
  always begin
    #1.75;
    forever #7 cclk <= ~cclk;
  end
  logic hrst_n = 0, crst_n = 0;
  logic h_enable = 1, c_enable = 1, h_rearm = 0, c_rearm = 0, h_fault, c_fault;
  logic h_request_valid = 0, h_request_ready, c_request_valid, c_request_ready = 0;
  lpddr5_mc_granule_t h_request = '0, c_request;
  logic [31:0] h_boot = 0, h_config = 0, c_boot, c_config;
  logic c_completion_valid = 0, c_completion_ready, h_completion_valid, h_completion_ready = 0;
  lpddr5_mc_host_completion_t c_completion = '0, h_completion;
  logic h_feedback_valid = 0, h_feedback_ready, c_feedback_valid, c_feedback_ready = 0;
  lpddr5_mc_host_feedback_t h_feedback = '0, c_feedback;
  request_record_t request_expected [COUNT + 16];
  lpddr5_mc_host_completion_t completion_expected [COUNT + 16];
  lpddr5_mc_host_feedback_t feedback_expected [COUNT * 2 + 16];
  int req_written = 0, req_read = 0, comp_written = 0, comp_read = 0, feed_written = 0, feed_read = 0;
  int requests = 0, completions = 0, feedback_records = 0;
  int checks = 0, hcycle = 0, ccycle = 0;
  int consumer_mode = 0;
  int stage = 0;
  logic request_stalled = 0, completion_stalled = 0, feedback_stalled = 0;
  request_record_t prior_request;
  lpddr5_mc_host_completion_t prior_completion;
  lpddr5_mc_host_feedback_t prior_feedback;
  request_record_t held_request;

  lpddr5_mc_host_channel_link #(.DEPTH(DEPTH)) dut (
    .hclk_i(hclk), .hrst_ni(hrst_n), .h_enable_i(h_enable), .h_rearm_i(h_rearm),
    .cclk_i(cclk), .crst_ni(crst_n), .c_enable_i(c_enable), .c_rearm_i(c_rearm),
    .h_fault_o(h_fault), .c_fault_o(c_fault), .h_rearm_ready_o(), .c_rearm_ready_o(),
    .h_request_valid_i(h_request_valid), .h_request_ready_o(h_request_ready), .h_request_i(h_request),
    .h_boot_epoch_i(h_boot), .h_config_epoch_i(h_config),
    .c_request_valid_o(c_request_valid), .c_request_ready_i(c_request_ready), .c_request_o(c_request),
    .c_boot_epoch_o(c_boot), .c_config_epoch_o(c_config),
    .c_completion_valid_i(c_completion_valid), .c_completion_ready_o(c_completion_ready), .c_completion_i(c_completion),
    .h_completion_valid_o(h_completion_valid), .h_completion_ready_i(h_completion_ready), .h_completion_o(h_completion),
    .h_feedback_valid_i(h_feedback_valid), .h_feedback_ready_o(h_feedback_ready), .h_feedback_i(h_feedback),
    .c_feedback_valid_o(c_feedback_valid), .c_feedback_ready_i(c_feedback_ready), .c_feedback_o(c_feedback)
  );
  task automatic check(input logic condition, input string explanation);
    assert(condition) else $fatal(1, "%s", explanation);
    checks++;
  endtask

  task automatic send_request(input int index, input logic [31:0] boot = 32'h10203040);
    @(negedge hclk);
    h_request.tag = 64'hfedcba9800000000 | 64'(index);
    h_request.axi_id = 16'(index ^ 'hff00);
    h_request.address = 40'hab00000000 | (40'(index) << 6);
    h_request.port_index = 1'(index);
    h_request.channel_index = 1'(index >> 1);
    h_request.rank_index = 1'(index >> 2);
    h_request.bank_group = 2'(index >> 3);
    h_request.bank = 2'(index >> 5);
    h_request.row = 18'(index * 17);
    h_request.column = 12'(index * 3);
    h_request.qos_class = 2'(index);
    h_request.is_write = 1'(index >> 1);
    h_request.is_64_bytes = 1'(index >> 2);
    h_request.generation = 16'h8000 | 16'(index);
    for (int byte_index = 0; byte_index < 64; byte_index++)
      h_request.data[byte_index * 8 +: 8] = 8'(byte_index * 7 + index);
    h_request.byte_enable = 64'hf0f00f0faa5555aa ^ 64'(index);
    h_boot = boot;
    h_config = 32'h50607080 | 32'(index);
    h_request_valid = 1;
    do @(posedge hclk); while(!h_request_ready);
    @(negedge hclk); h_request_valid = 0;
  endtask

  task automatic send_completion(input int index, input logic [31:0] boot = 32'habcd0123);
    @(negedge cclk);
    c_completion.identity = '{tag: 64'h8899aabb00000000 | 64'(index), generation: 16'hffff - 16'(index),
        boot_epoch: boot, config_epoch: 32'h44556600 | 32'(index)};
    c_completion.physical_done = 1'(index);
    c_completion.integrity_done = 1'(index >> 1);
    c_completion.failed = 1'(index >> 2);
    c_completion.is_write = 1'(index >> 3);
    c_completion.response = 2'(index);
    for (int byte_index = 0; byte_index < 64; byte_index++)
      c_completion.data[byte_index * 8 +: 8] = 8'(byte_index * 11 + index);
    c_completion_valid = 1;
    do @(posedge cclk); while(!c_completion_ready);
    @(negedge cclk); c_completion_valid = 0;
  endtask

  task automatic send_feedback(input int index, input logic [31:0] boot = 32'h98765432);
    @(negedge hclk);
    h_feedback.identity = '{tag: 64'hcafebabe00000000 | (64'(index) / 64'd2), generation: 16'(index / 2),
        boot_epoch: boot, config_epoch: 32'h76543210};
    h_feedback.exposed = !1'(index);
    h_feedback.handshaken = 1'(index);
    h_feedback.response = 2'(index >> 1);
    h_feedback_valid = 1;
    do @(posedge hclk); while(!h_feedback_ready);
    @(negedge hclk); h_feedback_valid = 0;
  endtask

  // Simulation-only scoreboards capture actual source handshakes and compare
  // full payloads on destination handshakes, independently of FIFO pointers.
  initial forever begin
    @(posedge hclk);
    if (h_request_valid && h_request_ready) begin
      request_expected[req_written] = '{boot_epoch: h_boot, config_epoch: h_config, granule: h_request};
      req_written++;
      requests++;
    end
    if (h_feedback_valid && h_feedback_ready) begin
      feedback_expected[feed_written] = h_feedback;
      feed_written++;
      feedback_records++;
    end
    if (h_completion_valid && h_completion_ready) begin
      check(comp_read < comp_written, "unsolicited completion");
      check(h_completion == completion_expected[comp_read], "completion identity/data/flags changed");
      comp_read++;
    end
    if (!h_fault && completion_stalled) check(h_completion_valid && h_completion == prior_completion,
        "stalled completion VALID/payload changed");
    completion_stalled = !h_fault && h_completion_valid && !h_completion_ready;
    prior_completion = h_completion;
  end
  initial forever begin
    @(posedge cclk);
    if (c_completion_valid && c_completion_ready) begin
      completion_expected[comp_written] = c_completion;
      comp_written++;
      completions++;
    end
    if (c_request_valid && c_request_ready) begin
      check(req_read < req_written, "unsolicited request");
      check({c_boot, c_config, c_request} == request_expected[req_read], "request sideband/granule changed");
      req_read++;
    end
    if (c_feedback_valid && c_feedback_ready) begin
      check(feed_read < feed_written, "unsolicited feedback");
      check(c_feedback == feedback_expected[feed_read], "exposure/handshake identity changed");
      feed_read++;
    end
    if (!c_fault && request_stalled) check(c_request_valid &&
        {c_boot, c_config, c_request} == prior_request, "stalled request changed");
    if (!c_fault && feedback_stalled) check(c_feedback_valid && c_feedback == prior_feedback,
        "stalled feedback changed");
    request_stalled = !c_fault && c_request_valid && !c_request_ready;
    feedback_stalled = !c_fault && c_feedback_valid && !c_feedback_ready;
    prior_request = {c_boot, c_config, c_request};
    prior_feedback = c_feedback;
  end
  initial forever begin
    @(negedge hclk);
    hcycle++;
    h_completion_ready = consumer_mode == 2 || (consumer_mode == 1 && hcycle % 7 != 0 && hcycle % 7 != 1);
  end
  initial forever begin
    @(negedge cclk);
    ccycle++;
    c_request_ready = consumer_mode == 2 || (consumer_mode == 1 && ccycle % 5 != 0);
    c_feedback_ready = consumer_mode == 2 || (consumer_mode == 1 && ccycle % 4 != 0);
  end

  task automatic rearm_both();
    repeat(5) @(negedge hclk);
    h_rearm = 1;
    @(negedge hclk); h_rearm = 0;
    repeat(5) @(negedge cclk);
    c_rearm = 1;
    @(negedge cclk); c_rearm = 0;
    wait(!h_fault && !c_fault && h_request_ready && c_completion_ready && h_feedback_ready);
  endtask

  task automatic reset_round(input logic reset_host);
    logic [31:0] fresh_boot;
    fresh_boot = 32'h00001001 + 32'(reset_host);
    stage = 10 + 10 * int'(reset_host);
    consumer_mode = 0;
    repeat(4) @(negedge cclk);
    // Explicit branches also keep timed task bodies grouped on the admitted
    // simulator, which otherwise expands bare calls as forked statements.
    fork
      begin send_request(COUNT + 2, 32'hdeadbeef); end
      begin send_completion(COUNT + 3, 32'hdeadbeef); end
      begin send_feedback(COUNT * 2 + 4, 32'hdeadbeef); end
    join
    wait(c_request_valid && h_completion_valid && c_feedback_valid);
    stage++;
    #0.75;
    if (reset_host) hrst_n = 0;
    else crst_n = 0;
    #0.1;
    check(h_fault && c_fault && !h_request_ready && !c_completion_ready && !h_feedback_ready &&
        !c_request_valid && !h_completion_valid && !c_feedback_valid,
        "endpoint reset did not fence all directions");
    // Abort only transport records. Real host acceptance-ledger settlement is
    // owned by the host RTL; the link cannot prove or replace that settlement.
    req_written = 0; req_read = 0; comp_written = 0; comp_read = 0; feed_written = 0; feed_read = 0;
    if (reset_host) begin
      @(negedge hclk); hrst_n = 1;
    end else begin
      @(negedge cclk); crst_n = 1;
    end
    repeat(6) @(negedge hclk);
    if (reset_host) begin
      @(negedge cclk); c_rearm = 1;
      @(negedge cclk); c_rearm = 0;
    end else begin
      h_rearm = 1;
      @(negedge hclk); h_rearm = 0;
    end
    repeat(6) @(negedge hclk);
    check((reset_host ? h_fault : c_fault) && !h_request_ready && !c_completion_ready && !h_feedback_ready,
        "one-sided rearm resumed transport");
    if (reset_host) begin
      h_rearm = 1;
      @(negedge hclk); h_rearm = 0;
    end else begin
      @(negedge cclk); c_rearm = 1;
      @(negedge cclk); c_rearm = 0;
    end
    wait(!h_fault && !c_fault && h_request_ready && c_completion_ready && h_feedback_ready);
    stage++;
    consumer_mode = 2;
    fork
      begin send_request(7, fresh_boot); end
      begin send_completion(8, fresh_boot); end
      begin send_feedback(10, fresh_boot); end
    join
    wait(req_read == 1 && comp_read == 1 && feed_read == 1);
    stage++;
    check(request_expected[0].boot_epoch == fresh_boot && completion_expected[0].identity.boot_epoch == fresh_boot &&
        feedback_expected[0].identity.boot_epoch == fresh_boot && req_written == 1 && comp_written == 1 && feed_written == 1,
        "stale reset payload replayed");
    repeat(8) @(negedge cclk);
    check(req_read == 1 && comp_read == 1 && feed_read == 1 &&
        !c_request_valid && !h_completion_valid && !c_feedback_valid, "old-epoch tail replayed after rearm");
  endtask
  initial begin
    repeat(3) @(negedge hclk);
    hrst_n = 1;
    @(negedge cclk); crst_n = 1;
    repeat(5) @(negedge hclk);
    check(h_fault && c_fault && !h_request_ready && !c_completion_ready && !h_feedback_ready,
        "initial reset bypassed recovery fence");
    rearm_both();
    stage = 1;
    fork
      for (int index = 0; index < COUNT; index++) send_request(index);
      for (int index = 0; index < COUNT; index++) send_completion(index);
      for (int index = 0; index < COUNT * 2; index++) send_feedback(index);
      begin
        repeat(DEPTH * 4 + 8) @(negedge hclk);
        check(req_written == DEPTH && comp_written == DEPTH && feed_written == DEPTH,
            "finite FIFO storage did not stop acceptance at capacity");
        check(!h_request_ready && !c_completion_ready && !h_feedback_ready, "full FIFO failed backpressure");
        consumer_mode = 1;
      end
    join
    stage = 2;
    wait(req_read == COUNT && comp_read == COUNT && feed_read == COUNT * 2);
    stage = 3;
    repeat(8) @(negedge hclk);
    check(!c_request_valid && !h_completion_valid && !c_feedback_valid, "duplicate tail transfer");
    consumer_mode = 0;
    repeat(3) @(negedge cclk);
    send_request(COUNT + 1);
    wait(c_request_valid);
    @(posedge cclk);
    #0.1;
    held_request = {c_boot, c_config, c_request};
    c_enable = 0;
    repeat(4) @(negedge cclk);
    check(c_request_valid && {c_boot, c_config, c_request} == held_request,
        "destination disable retracted visible request");
    c_enable = 1;
    consumer_mode = 2;
    wait(req_read == COUNT + 1);
    stage = 4;
    reset_round(0);
    reset_round(1);
    $display("ADAPTER_LINK_PASS depth=%0d requests=%0d completions=%0d feedback=%0d checks=%0d seed=2095 clocks=9ns/14ns phase=1.75ns",
        DEPTH, requests, completions, feedback_records, checks);
    $finish;
  end
  initial begin
    #200000;
    $fatal(1, "host/channel adapter deadline stage=%0d req=%0d/%0d comp=%0d/%0d feedback=%0d/%0d faults=%b%b mode=%0d",
        stage, req_read, req_written, comp_read, comp_written, feed_read, feed_written, h_fault, c_fault, consumer_mode);
  end
endmodule
