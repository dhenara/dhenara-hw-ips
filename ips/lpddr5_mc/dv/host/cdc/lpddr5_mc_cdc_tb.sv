`timescale 1ns/1ps
// Independent-clock transport tests; reference queues observe handshakes only.
/* verilator lint_off BLKSEQ */
module lpddr5_mc_cdc_tb #(
  parameter int unsigned DEPTH = 16,
  parameter int unsigned WIDTH = 80,
  parameter int unsigned W_HALF = 3,
  parameter int unsigned R_HALF = 5,
  parameter int unsigned R_PHASE = 2
);
  logic wclk = 0, rclk = 0;
  logic wrst_n = 0, rrst_n = 0;
  logic w_enable = 1, r_enable = 1, w_rearm = 0, r_rearm = 0;
  logic wvalid = 0, wready, rvalid, rready = 0, w_fault, r_fault;
  logic [WIDTH-1:0] wdata = '0, rdata;
  logic svalid = 0, sready, dvalid, dready = 0, s_fault, d_fault;
  logic [WIDTH-1:0] sdata = '0, ddata;
  logic r_clock_run = 1;
  logic [WIDTH-1:0] fifo_expected[$], mailbox_expected[$];
  int unsigned pushed = 0, popped = 0, mailed = 0, received = 0;
  int unsigned aborted_fifo = 0, aborted_mailbox = 0;
  int unsigned stage = 0;
  logic w_reset_ready, r_reset_ready, s_reset_ready, d_reset_ready, w_pending;
  logic [31:0] random_state = 32'd2095;

  always #(W_HALF) wclk = ~wclk;
  initial begin
    #(R_PHASE);
    forever begin
      #(R_HALF);
      if (r_clock_run) rclk = ~rclk;
    end
  end
  lpddr5_mc_async_fifo #(.WIDTH(WIDTH), .DEPTH(DEPTH)) fifo (
    .wclk_i(wclk), .wrst_ni(wrst_n), .w_enable_i(w_enable), .w_rearm_i(w_rearm),
    .w_pending_o(w_pending), .w_rearm_ready_o(w_reset_ready), .r_rearm_ready_o(r_reset_ready),
    .wvalid_i(wvalid), .wready_o(wready), .wdata_i(wdata), .w_fault_o(w_fault),
    .rclk_i(rclk), .rrst_ni(rrst_n), .r_enable_i(r_enable), .r_rearm_i(r_rearm),
    .rvalid_o(rvalid), .rready_i(rready), .rdata_o(rdata), .r_fault_o(r_fault)
  );
  lpddr5_mc_mailbox #(.WIDTH(WIDTH)) mailbox_dut (
    .sclk_i(wclk), .srst_ni(wrst_n), .s_enable_i(w_enable), .s_rearm_i(w_rearm),
    .s_rearm_ready_o(s_reset_ready), .d_rearm_ready_o(d_reset_ready),
    .svalid_i(svalid), .sready_o(sready), .sdata_i(sdata), .s_fault_o(s_fault),
    .dclk_i(rclk), .drst_ni(rrst_n), .d_enable_i(r_enable), .d_rearm_i(r_rearm),
    .dvalid_o(dvalid), .dready_i(dready), .ddata_o(ddata), .d_fault_o(d_fault)
  );

  function automatic logic [31:0] next_random(input logic [31:0] x);
    logic [31:0] y;
    y = x ^ (x << 13);
    y ^= y >> 17;
    return y ^ (y << 5);
  endfunction
  function automatic logic [WIDTH-1:0] pattern(input int unsigned value);
    logic [WIDTH-1:0] result;
    logic [31:0] x;
    x = value ^ 32'hbb6d2095;
    for (int i = 0; i < WIDTH; i++) begin
      x = next_random(x);
      result[i] = x[0];
    end
    return result;
  endfunction

  always @(posedge wclk) begin
    if (wvalid && wready) begin
      fifo_expected.push_back(wdata);
      pushed++;
    end
    if (svalid && sready) begin
      mailbox_expected.push_back(sdata);
      mailed++;
    end
  end
  always @(posedge rclk) begin
    logic [WIDTH-1:0] expected;
    if (rvalid && rready) begin
      assert (fifo_expected.size() > 0) else $fatal(1, "unsolicited FIFO output");
      expected = fifo_expected.pop_front();
      assert (rdata == expected) else $fatal(1, "FIFO lost/reordered/corrupted payload");
      popped++;
    end
    if (dvalid && dready) begin
      assert (mailbox_expected.size() > 0) else $fatal(1, "unsolicited mailbox output");
      expected = mailbox_expected.pop_front();
      assert (ddata == expected) else $fatal(1, "mailbox lost/reordered/corrupted payload");
      received++;
    end
  end
  // Payload and valid cannot change while the receiver applies backpressure.
  assert property (@(posedge rclk) disable iff (!rrst_n || !wrst_n || r_fault)
    rvalid && !rready |=> rvalid && $stable(rdata));
  assert property (@(posedge rclk) disable iff (!rrst_n || !wrst_n || d_fault)
    dvalid && !dready |=> dvalid && $stable(ddata));

  task automatic send_fifo(input int unsigned value);
    @(negedge wclk);
    wdata = pattern(value);
    wvalid = 1;
    do @(posedge wclk); while (!wready);
    @(negedge wclk);
    wvalid = 0;
    wdata = ~wdata;
  endtask
  task automatic send_mailbox(input int unsigned value);
    @(negedge wclk);
    sdata = pattern(value);
    svalid = 1;
    do @(posedge wclk); while (!sready);
    @(negedge wclk);
    svalid = 0;
    sdata = ~sdata;
  endtask
  task automatic rearm_both;
    @(negedge wclk);
    while (!(w_reset_ready && s_reset_ready)) @(negedge wclk);
    w_rearm = 1;
    @(negedge wclk);
    w_rearm = 0;
    @(negedge rclk);
    while (!(r_reset_ready && d_reset_ready)) @(negedge rclk);
    r_rearm = 1;
    @(negedge rclk);
    r_rearm = 0;
    while (!(wready && sready && !r_fault && !d_fault)) @(negedge wclk);
  endtask
  task automatic drain;
    @(negedge rclk);
    rready = 1;
    dready = 1;
    while (fifo_expected.size() != 0 || mailbox_expected.size() != 0) @(negedge rclk);
    repeat (8) @(negedge rclk);
    assert (!rvalid && !dvalid && !w_pending) else $fatal(1, "duplicate or pending output after drain");
  endtask

  initial begin
    #40;
    wrst_n = 1;
    #7;
    rrst_n = 1;
    repeat (10) @(negedge wclk);
    assert (!wready && !rvalid && !sready && !dvalid && w_fault && r_fault && s_fault && d_fault)
      else $fatal(1, "transport automatically resumed after reset");
    rearm_both();
    stage = 1;

    // Full capacity is usable, and a disabled receiver prevents consumption.
    r_enable = 0;
    for (int unsigned i = 0; i < DEPTH; i++) send_fifo(i);
    repeat (8) @(negedge wclk);
    assert (!wready && !rvalid && w_pending) else $fatal(1, "FIFO full/enable/pending handling");
    r_enable = 1;
    stage = 11;
    while (!rvalid) @(negedge rclk);
    stage = 12;
    repeat (2) @(negedge rclk);
    r_enable = 0;
    repeat (4) @(negedge rclk);
    assert (rvalid) else $fatal(1, "enable retracted exposed FIFO data");
    rready = 1;
    stage = 13;
    repeat (2) @(negedge rclk);
    assert (!rvalid) else $fatal(1, "disabled FIFO exposed a new head");
    r_enable = 1;
    stage = 14;
    drain();
    stage = 2;

    dready = 0;
    send_mailbox(32'h9000);
    while (!dvalid) @(negedge rclk);
    repeat (2) @(negedge rclk);
    r_enable = 0;
    repeat (4) @(negedge rclk);
    assert (dvalid) else $fatal(1, "enable retracted exposed mailbox data");
    dready = 1;
    repeat (4) @(negedge rclk);
    assert (!dvalid) else $fatal(1, "mailbox repeated disabled data");
    r_enable = 1;
    drain();
    stage = 3;

    // Hundreds of transfers wrap Gray pointers many times with independent
    // clocks and deterministic random receiver backpressure.
    fork
      begin
        for (int unsigned i = 0; i < 300; i++) send_fifo(32'h1000 + i);
      end
      begin
        for (int unsigned i = 0; i < 50; i++) send_mailbox(32'h2000 + i);
      end
      begin
        for (int unsigned i = 0; i < 1500; i++) begin
          @(negedge rclk);
          random_state = next_random(random_state);
          rready = random_state[0];
          dready = random_state[1];
        end
        rready = 1;
        dready = 1;
      end
    join
    stage = 4;
    drain();

    // Reset a stopped destination with accepted work pending. The independent
    // scoreboard records explicit abortions; a FIFO never claims completion.
    @(negedge rclk);
    rready = 0;
    dready = 0;
    send_fifo(32'h3000);
    send_mailbox(32'h4000);
    repeat (5) @(negedge rclk);
    assert (rvalid && dvalid) else $fatal(1, "pending reset case not exercised");
    r_clock_run = 0;
    #1;
    rrst_n = 0;
    #1;
    assert (!wready && !rvalid && !sready && !dvalid && w_fault && r_fault && s_fault && d_fault)
      else $fatal(1, "endpoint reset failed immediate fencing");
    assert (!(w_reset_ready || r_reset_ready || s_reset_ready || d_reset_ready))
      else $fatal(1, "reset readiness failed immediate assertion");
    aborted_fifo += int'(fifo_expected.size());
    aborted_mailbox += int'(mailbox_expected.size());
    fifo_expected.delete();
    mailbox_expected.delete();
    #2;
    rrst_n = 1;
    repeat (12) @(negedge wclk);
    assert (!wready && !sready && w_fault && r_fault && s_fault && d_fault) else $fatal(1, "reset pulse was lost");
    assert (w_reset_ready && s_reset_ready && !r_reset_ready && !d_reset_ready)
      else $fatal(1, "stopped domain advertised reset release");
    // A pulse while the stopped domain is still in its actual local reset
    // must be ignored. Its first restarted edge is also before release.
    r_rearm = 1;
    r_clock_run = 1;
    @(negedge rclk);
    assert (!r_reset_ready && !d_reset_ready)
      else $fatal(1, "destination reset released before two local edges");
    r_rearm = 0;
    while (!(r_reset_ready && d_reset_ready)) @(negedge rclk);
    assert (r_fault && d_fault) else $fatal(1, "early rearm escaped reset fencing");
    @(negedge wclk);
    w_rearm = 1;
    @(negedge wclk);
    w_rearm = 0;
    repeat (10) @(negedge wclk);
    assert (!wready && !sready && r_fault && d_fault)
      else $fatal(1, "one-sided or early rearm resumed transport");
    rearm_both();
    stage = 5;
    rready = 1;
    dready = 1;
    fork
      begin
        for (int unsigned i = 0; i < 100; i++) send_fifo(32'h5000 + i);
      end
      begin
        for (int unsigned i = 0; i < 20; i++) send_mailbox(32'h6000 + i);
      end
    join
    stage = 6;
    drain();

    // Reverse endpoint reset also fences both directions; no stale toggle
    // or payload survives into the freshly rearmed transport generation.
    #1;
    wrst_n = 0;
    #2;
    wrst_n = 1;
    repeat (12) @(negedge rclk);
    assert (!wready && !rvalid && !sready && !dvalid && w_fault && r_fault && s_fault && d_fault)
      else $fatal(1, "source reset did not remain fenced");
    rearm_both();
    stage = 7;
    send_fifo(32'h7000);
    send_mailbox(32'h8000);
    drain();
    assert (pushed == popped + aborted_fifo && mailed == received + aborted_mailbox)
      else $fatal(1, "accepted transfer accounting");
    assert (aborted_fifo == 1 && aborted_mailbox == 1) else $fatal(1, "missing reset abortion witness");
    $display("CDC_PASS depth=%0d width=%0d clocks=%0d/%0d phase=%0d fifo=%0d mailbox=%0d aborted=%0d/%0d seed=2095",
      DEPTH, WIDTH, W_HALF, R_HALF, R_PHASE, popped, received, aborted_fifo, aborted_mailbox);
    $finish;
  end
  initial begin
    #1000000;
    $display("FIFO debug enable=%b valid=%b ready=%b pointers=%0d/%0d remote=%0d empty=%b run=%b reset=%b",
      r_enable, rvalid, rready, fifo.wbin_q, fifo.rbin_q, fifo.wgray_r2, fifo.empty_q, fifo.r_run, fifo.r_reset_n);
    $fatal(1, "CDC timeout stage=%0d counts=%0d/%0d %0d/%0d pending=%0d/%0d ready=%b/%b faults=%b%b%b%b",
      stage, pushed, popped, mailed, received, fifo_expected.size(), mailbox_expected.size(),
      wready, sready, w_fault, r_fault, s_fault, d_fault);
  end
endmodule
/* verilator lint_on BLKSEQ */
