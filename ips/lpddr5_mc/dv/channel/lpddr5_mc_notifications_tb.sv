module lpddr5_mc_notifications_tb;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0,rst_n=0,reset_volatile=0,ready=0,valid,overflow,clear=0,first_valid;
  logic [2:0] occurrence_valid=0;
  logic [63:0] clear_version=0,first_version;
  lpddr5_mc_channel_notification_t [2:0] occurrences;
  lpddr5_mc_channel_notification_t notification,first_error,held;
  lpddr5_mc_notifications #(.DEPTH(4)) dut (
    .clk_ck_i(clk),.rst_ni(rst_n),.destructive_reset_i(reset_volatile),
    .occurrence_valid_i(occurrence_valid),.occurrence_i(occurrences),
    .notification_valid_o(valid),.notification_ready_i(ready),.notification_o(notification),.overflow_o(overflow),
    .first_error_clear_i(clear),.first_error_clear_version_i(clear_version),
    .first_error_valid_o(first_valid),.first_error_version_o(first_version),.first_error_o(first_error));
  logic unused_debug;
  assign unused_debug=^first_error;
  task automatic tick; #1;clk=1;#1;clk=0;#1;endtask
  function automatic lpddr5_mc_channel_notification_t event_value(input int tag_value,input bit error_value=1);
    lpddr5_mc_channel_notification_t n;
    n='0;n.events=error_value ? 13'h20 : 13'h04;n.boot_epoch=3;n.config_epoch=9;n.error_valid=error_value;
    n.error.tag=64'(tag_value);n.error.address=40'(tag_value)*64;n.error.axi_id=16'(tag_value);n.error.correlated=error_value;n.error.memory_trusted=1;
    return n;
  endfunction
  task automatic send(input int tag_value,input bit error_value=1);
    occurrences[0]=event_value(tag_value,error_value);occurrence_valid=1;tick();occurrence_valid=0;
  endtask
  task automatic take(input int tag_value);
    #1;assert(valid && notification.error.tag==64'(tag_value) && notification.boot_epoch==3 && notification.config_epoch==9)
      else $fatal(1,"notification order/occurrence identity expected=%0d got=%0d",tag_value,notification.error.tag);
    ready=1;tick();ready=0;
  endtask
  initial begin
    for(int p=0;p<3;p++) occurrences[p]='0;
    tick();rst_n=1;
    send(1);held=notification;
    assert(valid && first_valid && first_error.error.tag==1 && first_version==1) else $fatal(1,"first occurrence capture");
    repeat(8) begin tick();assert(valid && notification==held && first_version==1) else $fatal(1,"immutable stalled occurrence");end
    // Same-bit occurrences retain multiplicity. Three unrelated producers may
    // notify on the same CK without collapsing their independent error detail.
    for(int p=0;p<3;p++) occurrences[p]=event_value(p+2);
    occurrence_valid=7;tick();occurrence_valid=0;
    assert(!overflow && first_error.error.tag==1) else $fatal(1,"full queue/first capture");
    // Full queue pop plus arrival preserves capacity and order.
    occurrences[0]=event_value(5);occurrence_valid=1;ready=1;tick();occurrence_valid=0;ready=0;
    take(2);take(3);take(4);take(5);assert(!valid) else $fatal(1,"repeated event count");
    repeat(5) begin tick();assert(!valid) else $fatal(1,"snapshot fabricated notification");end
    // Destructive recovery does not flush diagnostic state or relabel old
    // queued events. Re-armed detail comes from each new immutable occurrence.
    send(6);reset_volatile=1;tick();reset_volatile=0;
    assert(first_valid && first_error.error.tag==1 && first_version==1) else $fatal(1,"recovery lost diagnostic");
    take(6);
    clear=1;clear_version=99;tick();clear=0;
    assert(first_valid && first_version==1) else $fatal(1,"stale clear version");
    clear=1;clear_version=1;occurrences[0]=event_value(7);occurrence_valid=1;tick();clear=0;occurrence_valid=0;
    assert(first_valid && first_error.error.tag==7 && first_version==2) else $fatal(1,"clear/set winner");
    clear=1;clear_version=1;tick();clear=0;assert(first_valid && first_version==2) else $fatal(1,"delayed clear erased new error");
    take(7);clear=1;clear_version=2;tick();clear=0;assert(!first_valid) else $fatal(1,"exact clear");
    send(8,0);assert(!first_valid) else $fatal(1,"ordinary event captured error");take(8);
    // Fail closed on overflow, retaining the stalled head and reporting the
    // detail loss with its actual old boot/configuration identity.
    for(int n=9;n<=12;n++) send(n);
    held=notification;send(13);
    assert(overflow && valid && notification==held) else $fatal(1,"overflow replaced stalled head");
    for(int n=9;n<=12;n++) take(n);
    #1;assert(valid && notification.events==13'h0a00 && !notification.error_valid && notification.boot_epoch==3 && notification.config_epoch==9)
      else $fatal(1,"overflow retained report");
    reset_volatile=1;tick();reset_volatile=0;
    assert(!overflow && valid && notification.events==13'h0a00 && first_valid && first_error.error.tag==9) else $fatal(1,"volatile recovery touched retained notification");
    ready=1;tick();ready=0;assert(!valid) else $fatal(1,"overflow report duplicate");
    $display("PASS notifications: repeated bits, three producers, stalls, consume/set, original epochs, versioned rearm and fail-closed overflow");$finish;
  end
endmodule
