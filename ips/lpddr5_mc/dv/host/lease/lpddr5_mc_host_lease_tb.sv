`timescale 1ns/1ps
/* verilator lint_off BLKSEQ */
// Source handshakes define this oracle. No lease mask or import order seeds it.
module lpddr5_mc_host_lease_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_host_pkg::*;
  import lpddr5_mc_axi_pkg::*;
  localparam int MAX_TRANSACTIONS=256, BUS_BYTES=16;
  /* verilator lint_off SYNCASYNCNET */
  logic clk=0,rst_n=0,enable=0,fatal=0,recover=0,cfg_valid=0,cfg_ready;
  /* verilator lint_on SYNCASYNCNET */
  logic clk0=0,clk1=0,run_host=1,run0=1,run1=1;
  wire [1:0] axi_clk={clk1,clk0};
  logic [1:0] axi_rst_n=0;
  logic rearm=0,rearm_ready,initialized,config_valid,fault,settled,late_error;
  logic [1:0] awvalid=0,arvalid=0,wvalid=0,awready,arready,wready;
  logic [1:0] bvalid,rvalid,bready=3,rready=3;
  lpddr5_mc_axi_address_t [1:0] aw,ar;
  lpddr5_mc_axi_write_t [1:0] wd;
  lpddr5_mc_axi_b_t [1:0] b;
  lpddr5_mc_axi_read_t [1:0] r;
  lpddr5_mc_host_config_t cfg;
  logic [1:0] request_valid,request_ready,completion_valid=0,completion_ready;
  logic [1:0] feedback_valid,feedback_ready=3;
  lpddr5_mc_granule_t [1:0] request,pending;
  lpddr5_mc_host_completion_t [1:0] completion;
  lpddr5_mc_host_feedback_t [1:0] feedback;
  logic [1:0][31:0] request_boot,request_config,pending_boot,pending_config;
  logic [31:0] stale;
  logic [63:0] accepted_addresses,grant_cycles;
  logic [7:0] memory[65536],golden[65536];
  logic [1:0] busy=0,physical_sent=0;
  int channel_delay[2],service_delay=3;
  int source_next[2],import_next[2];
  int accepted=0,imported=0,reference_cursor=0,requests=0,tests=0,stage=0;
  bit measure_case=0,measure_active=0,measure_done=0;
  int case_completed=0,window_warmup=0,window_completions=0;
  time measure_start_ps=0,measure_end_ps=0;
  longint unsigned measure_bytes=0;
  int cutoff_samples=0,command_samples=0,ack_samples=0;
  bit inject_command=0,inject_ack=0;
  bit corrupt_read=0,corrupt_memory=0,corrupted_read=0,corrupted_memory=0;
  int measure_cycles[2],measure_awvalid[2],measure_awready[2],measure_awhandshakes[2];
  int measure_wvalid[2],measure_wready[2],measure_whandshakes[2];
  typedef struct packed {
    logic write_access,write_error,fatal_seen,b_exposed;
    int port_index,source_sequence,w_received,b_seen,r_seen,import_ordinal;
    logic [31:0] boot_epoch;
    time accepted_at_ps,b_handshake_at_ps;
    lpddr5_mc_axi_address_t address;
  } observed_t;
  observed_t [MAX_TRANSACTIONS-1:0] observed;
  lpddr5_mc_axi_write_t observed_w[MAX_TRANSACTIONS][256];
  lpddr5_mc_axi_read_t observed_r[MAX_TRANSACTIONS][256];
  lpddr5_mc_axi_b_t observed_b[MAX_TRANSACTIONS];

  always #0.625 if(run_host)clk=~clk;
  always #1.25 if(run0)clk0=~clk0;
  initial begin #0.375;forever #1.25 if(run1)clk1=~clk1;end
  lpddr5_mc_host #(.DEPTH(16)) dut (
    .clk_i(clk),.rst_ni(rst_n),.enable_i(enable),.fatal_i(fatal),.recover_i(recover),
    .cfg_valid_i(cfg_valid),.cfg_ready_o(cfg_ready),.cfg_i(cfg),.rearm_i(rearm),
    .rearm_ready_o(rearm_ready),.initialized_o(initialized),.config_valid_o(config_valid),
    .fault_o(fault),.host_settled_o(settled),.late_error_o(late_error),.stale_completions_o(stale),
    .axi_clk_i(axi_clk),.axi_rst_ni(axi_rst_n),.awvalid_i(awvalid),.arvalid_i(arvalid),.wvalid_i(wvalid),
    .awready_o(awready),.arready_o(arready),.wready_o(wready),.aw_i(aw),.ar_i(ar),.w_i(wd),
    .bvalid_o(bvalid),.rvalid_o(rvalid),.bready_i(bready),.rready_i(rready),.b_o(b),.r_o(r),
    .request_valid_o(request_valid),.request_ready_i(request_ready),.request_o(request),
    .request_boot_epoch_o(request_boot),.request_config_epoch_o(request_config),
    .completion_valid_i(completion_valid),.completion_ready_o(completion_ready),.completion_i(completion),
    .feedback_valid_o(feedback_valid),.feedback_ready_i(feedback_ready),.feedback_o(feedback),
    .accepted_addresses_o(accepted_addresses),.grant_cycles_o(grant_cycles)
  );
  // Arithmetic here is independent of the production splitter and mask helper.
  function automatic longint unsigned at_byte(input lpddr5_mc_axi_address_t a,input int beat);
    longint unsigned width_bytes,span,base;
    width_bytes=64'd1<<a.size;
    if(a.burst==0 || beat==0 && a.burst!=2)return 64'(a.address);
    if(a.burst==1)return (64'(a.address)/width_bytes+64'(beat))*width_bytes;
    span=width_bytes*(64'(a.len)+1);base=(64'(a.address)/span)*span;
    return base+(64'(a.address)-base+64'(beat)*width_bytes)%span;
  endfunction
  function automatic longint unsigned end_byte(input lpddr5_mc_axi_address_t a,input int beat);
    longint unsigned width_bytes;
    width_bytes=64'd1<<a.size;
    return (at_byte(a,beat)/width_bytes+1)*width_bytes;
  endfunction
  function automatic logic [31:0] allowed(input lpddr5_mc_axi_address_t a,input int beat);
    logic [31:0] mask;
    mask=0;
    for(int j=0;j<BUS_BYTES;j++)if(at_byte(a,beat)+64'(j)<end_byte(a,beat))
      mask[5'((at_byte(a,beat)+64'(j))%64'(BUS_BYTES))]=1;
    return mask;
  endfunction
  function automatic bit valid_form(input lpddr5_mc_axi_address_t a);
    if(a.size>4 || a.burst>2 || a.id[15:8]!=0)return 0;
    if(a.burst!=1 && a.len>15)return 0;
    if(a.burst==2 && (!(int'(a.len)+1 inside {2,4,8,16}) || 64'(a.address)%(64'd1<<a.size)!=0))return 0;
    for(int beat=0;beat<=int'(a.len);beat++)begin
      if(end_byte(a,beat)>65536 || at_byte(a,beat)/4096!=64'(a.address)/4096
          || (end_byte(a,beat)-1)/4096!=64'(a.address)/4096)return 0;
    end
    return 1;
  endfunction
  function automatic bit overlap(input lpddr5_mc_axi_address_t a,input lpddr5_mc_axi_address_t d);
    for(int i=0;i<=int'(a.len);i++)for(int j=0;j<=int'(d.len);j++)
      if(at_byte(a,i)<end_byte(d,j) && at_byte(d,j)<end_byte(a,i))return 1;
    return 0;
  endfunction
  // Enumerate addressed bytes rather than calling the implementation's interval helper.
  function automatic logic [63:0] source_footprint(input lpddr5_mc_axi_address_t a);
    logic [63:0] result;
    result=0;
    if(!valid_form(a))return '1;
    for(int beat=0;beat<=int'(a.len);beat++)for(int lane=0;lane<BUS_BYTES;lane++)begin
      longint unsigned at;
      at=at_byte(a,beat)+64'(lane);
      if(at<end_byte(a,beat))result[int'((at/64)%64)]=1;
    end
    return result;
  endfunction
  function automatic int transaction(input int p,id,input bit write_access);
    for(int i=0;i<accepted;i++)if(observed[i].port_index==p && observed[i].address.id==16'(id)
      && observed[i].write_access==write_access)return i;
    return -1;
  endfunction
  task automatic accepted_address(input int p,input bit write_access,input lpddr5_mc_axi_address_t a);
    if(accepted>=MAX_TRANSACTIONS)$fatal(1,"oracle transaction capacity");
    observed[accepted]='0;observed[accepted].address=a;observed[accepted].port_index=p;
    observed[accepted].write_access=write_access;observed[accepted].source_sequence=source_next[p];
    observed[accepted].boot_epoch=cfg.boot_epoch;
    observed[accepted].accepted_at_ps=time'($realtime/1ps);observed[accepted].import_ordinal=-1;
    $display("LEASE_ACCEPT ordinal=%0d port=%0d write=%0b id=%0d at=%0t",accepted,p,write_access,a.id,$realtime);
    source_next[p]++;accepted++;
  endtask
  for(genvar p=0;p<2;p++)begin:source_monitors
    always @(posedge axi_clk[p])if(axi_rst_n[p])begin
      int owner;
      if(measure_active)begin
        measure_cycles[p]++;
        if(awvalid[p])measure_awvalid[p]++;
        if(awready[p])measure_awready[p]++;
        if(awvalid[p]&&awready[p])measure_awhandshakes[p]++;
        if(wvalid[p])measure_wvalid[p]++;
        if(wready[p])measure_wready[p]++;
        if(wvalid[p]&&wready[p])measure_whandshakes[p]++;
      end
      if(awvalid[p]&&awready[p])accepted_address(p,1,aw[p]);
      if(arvalid[p]&&arready[p])accepted_address(p,0,ar[p]);
      if(awvalid[p]&&awready[p] || arvalid[p]&&arready[p])begin
        logic [63:0] footprint;
        footprint=source_footprint(awvalid[p]&&awready[p]?aw[p]:ar[p]);
        if(dut.bridge.ports[p].endpoint.lease_frozen_q
          ||(footprint&dut.bridge.ports[p].endpoint.owned_mask_q)!=footprint)
          $fatal(1,"source accepted without independent complete-footprint ownership");
      end
      if(dut.bridge.ports[p].endpoint.lease_local_valid
        &&dut.bridge.ports[p].endpoint.lease_local.revoke)begin
        if(awready[p]||arready[p])$fatal(1,"address READY on exposed revoke-head edge");
        if(awvalid[p]||arvalid[p])cutoff_samples++;
      end
      if(dut.bridge.ports[p].endpoint.lease_local_valid
        &&dut.bridge.ports[p].endpoint.lease_local_ready)begin
        command_samples++;
        if(!inject_command && (dut.bridge.ports[p].endpoint.lease_local.token
            !=dut.bridge.ports[p].endpoint.lease_token_q+1
          ||dut.bridge.ports[p].endpoint.lease_local.boot_epoch!=cfg.boot_epoch
          ||dut.bridge.ports[p].endpoint.lease_local.config_epoch!=cfg.config_epoch))
          $fatal(1,"command token or epoch changed across real CDC");
      end
      if(dut.bridge.ports[p].endpoint.lease_ack_local_valid
        &&dut.bridge.ports[p].endpoint.lease_ack_local_ready
        &&dut.bridge.ports[p].endpoint.lease_ack_q.command.revoke)begin
        int next_sequence;
        next_sequence=0;
        for(int i=0;i<accepted;i++)if(observed[i].port_index==p && observed[i].boot_epoch==cfg.boot_epoch)begin
          next_sequence=observed[i].source_sequence+1;
          if(observed[i].write_access?observed[i].b_seen!=1:
              observed[i].r_seen!=int'(observed[i].address.len)+1)
            $fatal(1,"revoke ACK preceded a real source response handshake");
        end
        if(dut.bridge.ports[p].endpoint.lease_ack_q.accepted_next_sequence!=32'(next_sequence))
          $fatal(1,"revoke ACK did not freeze the actual source acceptance watermark");
      end
      if(wvalid[p]&&wready[p])begin
        owner=-1;
        for(int i=0;i<accepted;i++)if(owner<0 && observed[i].port_index==p && observed[i].write_access
          && observed[i].w_received<=int'(observed[i].address.len))owner=i;
        if(owner<0)$fatal(1,"W handshake without admitted AW");
        observed_w[owner][observed[owner].w_received]=wd[p];
        if((wd[p].strobe&~allowed(observed[owner].address,observed[owner].w_received))!=0
            ||wd[p].last!=(observed[owner].w_received==int'(observed[owner].address.len)))observed[owner].write_error=1;
        observed[owner].w_received++;
      end
      if(bvalid[p])begin
        owner=-1;
        for(int i=0;i<accepted;i++)if(owner<0&&observed[i].port_index==p&&observed[i].write_access&&observed[i].b_seen==0)owner=i;
        if(owner<0)$fatal(1,"B without accepted transaction");
        if(!fatal&&b[p].response==0)observed[owner].b_exposed=1;
        if(bready[p])begin
          observed_b[owner]=b[p];observed[owner].b_seen=1;observed[owner].b_handshake_at_ps=time'($realtime/1ps);
          if(measure_case)begin
            case_completed++;
            if(case_completed==window_warmup)begin measure_active=1;measure_start_ps=time'($realtime/1ps);end
            else if(case_completed>window_warmup&&case_completed<=window_warmup+window_completions)begin
              measure_bytes+=64;
              if(case_completed==window_warmup+window_completions)begin
                measure_end_ps=time'($realtime/1ps);measure_active=0;measure_done=1;
              end
            end
          end
        end
      end
      if(rvalid[p]&&rready[p])begin
        owner=-1;
        for(int i=0;i<accepted;i++)if(owner<0&&observed[i].port_index==p&&!observed[i].write_access
          &&observed[i].r_seen<=int'(observed[i].address.len))owner=i;
        if(owner<0)$fatal(1,"R without accepted transaction");
        observed_r[owner][observed[owner].r_seen]=r[p];observed[owner].r_seen++;
      end
    end
    assert property(@(posedge axi_clk[p])disable iff(!axi_rst_n[p])
      bvalid[p]&&!bready[p]|=>bvalid[p]&&$stable(b[p]));
    assert property(@(posedge axi_clk[p])disable iff(!axi_rst_n[p])
      rvalid[p]&&!rready[p]|=>rvalid[p]&&$stable(r[p]));
  end
  always @(posedge fatal or posedge fault)for(int i=0;i<accepted;i++)
    if(observed[i].write_access?observed[i].b_seen==0:observed[i].r_seen<=int'(observed[i].address.len))observed[i].fatal_seen=1;
  // The import checker knows source sequence/payload, not lease computation.
  always @(posedge clk)if(rst_n)for(int p=0;p<2;p++)begin
    lpddr5_mc_host_address_t value;
    logic importing,write_access;
    int index;
    if((dut.bridge.owner_mask_q[0]&dut.bridge.owner_mask_q[1])!=0)
      $fatal(1,"simultaneous conflicting core ownership");
    if(!enable&&initialized&&!fault)for(int i=0;i<accepted;i++)
      if(observed[i].import_ordinal<0&&!observed[i].fatal_seen&&!dut.admission_busy)
        $fatal(1,"disable acknowledged while observed accepted descriptors still await import");
    if(dut.bridge.lease_ack_valid[p]&&dut.bridge.lease_ack_ready[p])begin
      ack_samples++;
      if(!inject_ack && (!dut.bridge.command_pending_q[p]
        ||dut.bridge.lease_acks[p].command!==dut.bridge.expected_command_q[p]))
        $fatal(1,"ACK identity was not preserved across CDC");
    end
    importing=(dut.aw_valid[p]&&dut.aw_ready[p])||(dut.ar_valid[p]&&dut.ar_ready[p]);
    write_access=dut.aw_valid[p]&&dut.aw_ready[p];value=write_access?dut.aw[p]:dut.ar[p];
    if(importing)begin
      index=transaction(p,int'(value.axi.id),write_access);
      if(index<0||observed[index].import_ordinal>=0||observed[index].address!==value.axi
        ||observed[index].source_sequence!=int'(value.sequence_number)||import_next[p]!=int'(value.sequence_number))
        $fatal(1,"import lost/duplicated/reordered source record");
      if(dut.bridge.accepted[p].footprint!==source_footprint(value.axi)
        ||dut.bridge.accepted[p].boot_epoch!=cfg.boot_epoch
        ||dut.bridge.accepted[p].config_epoch!=cfg.config_epoch)
        $fatal(1,"import footprint or epoch did not match independent source observation");
      for(int old=0;old<index;old++)if(observed[old].import_ordinal<0&&!observed[old].fatal_seen
        &&valid_form(observed[old].address)&&valid_form(value.axi)&&overlap(observed[old].address,value.axi))
        $fatal(1,"import crossed older unimported overlapping source transaction");
      observed[index].import_ordinal=imported;imported++;import_next[p]++;
      $display("LEASE_IMPORT source=%0d ordinal=%0d port=%0d at=%0t",index,imported-1,p,$realtime);
    end
  end
  assign request_ready=~busy&{2{rst_n&&!fatal}};
  always @(posedge clk)for(int c=0;c<2;c++)begin
    if(!rst_n||fatal||fault)busy[c]=0;
    else begin
      if(completion_valid[c]&&completion_ready[c]&&completion[c].integrity_done)busy[c]=0;
      if(request_valid[c]&&request_ready[c])begin
        int index;
        index=transaction(int'(request[c].port_index),int'(request[c].axi_id),request[c].is_write);
        if(index<0||!valid_form(observed[index].address)||busy[c])$fatal(1,"unexpected memory request");
        if(request[c].is_write && (observed[index].w_received!=int'(observed[index].address.len)+1||observed[index].write_error))
          $fatal(1,"write effects precede complete validated W");
        for(int old=0;old<index;old++)if(observed[old].write_access
          &&observed[old].w_received<=int'(observed[old].address.len)&&overlap(observed[old].address,observed[index].address))
          $fatal(1,"older AW hazard released while W missing");
        pending[c]=request[c];pending_boot[c]=request_boot[c];pending_config[c]=request_config[c];
        busy[c]=1;physical_sent[c]=0;channel_delay[c]=service_delay;requests++;
      end
    end
  end
  always @(negedge clk)begin
    completion_valid=0;
    for(int c=0;c<2;c++)if(busy[c]&&rst_n&&!fatal)begin
      if(channel_delay[c]>0)channel_delay[c]--;
      else begin
        completion[c]='0;completion[c].identity.tag=pending[c].tag;
        completion[c].identity.generation=pending[c].generation;
        completion[c].identity.boot_epoch=pending_boot[c];completion[c].identity.config_epoch=pending_config[c];
        completion[c].is_write=pending[c].is_write;completion[c].physical_done=1;
        completion[c].integrity_done=physical_sent[c];
        for(int j=0;j<64;j++)begin
          int at;
          at=int'(pending[c].address)+j;if(at>=65536)$fatal(1,"memory bounds");
          if(!physical_sent[c]&&pending[c].is_write&&pending[c].byte_enable[j])begin
            memory[at]=pending[c].data[j*8+:8];
            if(corrupt_memory&&!corrupted_memory)begin memory[at]^=8'h1;corrupted_memory=1;end
          end
          completion[c].data[j*8+:8]=memory[at];
        end
        if(corrupt_read&&!corrupted_read&&!pending[c].is_write&&completion[c].integrity_done)begin
          completion[c].data[7:0]^=8'h1;corrupted_read=1;
        end
        completion_valid[c]=1;physical_sent[c]=1;channel_delay[c]=service_delay;
      end
    end
  end
  task automatic source_neg(input int p);
    if(p==0)@(negedge clk0);else @(negedge clk1);
  endtask
  task automatic source_pos(input int p);
    if(p==0)@(posedge clk0);else @(posedge clk1);
  endtask
  function automatic lpddr5_mc_axi_address_t address(input int id,at,beats,size=4,burst=1);
    lpddr5_mc_axi_address_t a;
    a='0;a.id=16'(id);a.address=40'(at);a.len=8'(beats-1);a.size=3'(size);a.burst=2'(burst);return a;
  endfunction
  task automatic send_address(input int p,input bit write_access,input lpddr5_mc_axi_address_t a);
    int timeout;
    source_neg(p);
    if(write_access)begin aw[p]=a;awvalid[p]=1;end else begin ar[p]=a;arvalid[p]=1;end
    timeout=0;
    do begin source_pos(p);timeout++;if(timeout>3000)$fatal(1,"address timeout stage=%0d port=%0d",stage,p);end
    while(!(write_access?awready[p]:arready[p]));
    source_neg(p);if(write_access)awvalid[p]=0;else arvalid[p]=0;
  endtask
  task automatic send_write(input int p,input lpddr5_mc_axi_address_t a,input int seed,first=0,last=-1,mode=0);
    lpddr5_mc_axi_write_t value;
    int final_beat,timeout;
    final_beat=last<0?int'(a.len):last;
    for(int beat=first;beat<=final_beat;beat++)begin
      value='0;value.strobe=allowed(a,beat);value.last=beat==int'(a.len);
      for(int j=0;j<BUS_BYTES;j++)value.data[j*8+:8]=8'(seed+beat*17+j*11);
      if(mode==1)value.strobe&=32'h5555;
      if(mode==2&&beat==0)value.last=1;
      source_neg(p);wd[p]=value;wvalid[p]=1;timeout=0;
      do begin source_pos(p);timeout++;if(timeout>3000)$fatal(1,"W timeout");end while(!wready[p]);
      source_neg(p);wvalid[p]=0;
    end
  endtask
  task automatic stream_addresses(input int p,count,base,id_base);
    source_neg(p);awvalid[p]=1;
    for(int i=0;i<count;i++)begin
      aw[p]=address(id_base+i,base+(i%7)*8192,4);
      do source_pos(p);while(!awready[p]);
      source_neg(p);
    end
    awvalid[p]=0;
  endtask
  task automatic stream_data(input int p,count,seed);
    lpddr5_mc_axi_write_t value;
    source_neg(p);wvalid[p]=1;
    for(int burst=0;burst<count;burst++)for(int beat=0;beat<4;beat++)begin
      value='0;value.strobe=32'hffff;value.last=beat==3;
      for(int lane=0;lane<BUS_BYTES;lane++)value.data[lane*8+:8]=8'(seed+burst*7+beat*17+lane*11);
      wd[p]=value;
      do source_pos(p);while(!wready[p]);
      source_neg(p);
    end
    wvalid[p]=0;
  endtask
  task automatic wait_settled;
    int timeout;
    timeout=0;
    do begin @(negedge clk);timeout++;if(timeout>10000)$fatal(1,"settlement timeout stage=%0d",stage);end while(!settled);
    repeat(4)@(negedge clk);
    if(!settled)$fatal(1,"unstable host settlement stage=%0d",stage);
  endtask
  task automatic wait_responses;
    int timeout;
    bit complete;
    timeout=0;complete=0;
    while(!complete)begin
      @(negedge clk);complete=1;timeout++;
      for(int i=reference_cursor;i<accepted;i++)begin
        complete&=observed[i].write_access?observed[i].b_seen==1:
          observed[i].r_seen==int'(observed[i].address.len)+1;
      end
      if(timeout>10000)$fatal(1,"source response timeout stage=%0d",stage);
    end
  endtask
  task automatic check_reference(input string name,input bit count_family=1);
    wait_responses();
    wait_settled();
    for(int i=reference_cursor;i<accepted;i++)begin
      logic [1:0] response;
      response=!valid_form(observed[i].address)?2'b11:(observed[i].write_error?2'b10:2'b00);
      if(observed[i].fatal_seen&&response==0&&!(observed[i].write_access&&observed[i].b_exposed))response=2;
      if(observed[i].write_access)begin
        if(observed[i].w_received!=int'(observed[i].address.len)+1||observed[i].b_seen!=1
          ||observed_b[i].id!=observed[i].address.id||observed_b[i].response!=response)$fatal(1,"oracle B/count mismatch transaction=%0d",i);
        if(response==0)for(int beat=0;beat<=int'(observed[i].address.len);beat++)for(int j=0;j<BUS_BYTES;j++)begin
          longint unsigned at;
          int lane;
          at=at_byte(observed[i].address,beat)+64'(j);lane=int'(at%64'(BUS_BYTES));
          if(at<end_byte(observed[i].address,beat)&&observed_w[i][beat].strobe[lane])golden[16'(at)]=observed_w[i][beat].data[lane*8+:8];
        end
      end else begin
        if(observed[i].r_seen!=int'(observed[i].address.len)+1)$fatal(1,"oracle R count mismatch");
        for(int beat=0;beat<=int'(observed[i].address.len);beat++)begin
          logic [255:0] data;
          data=0;
          if(response==0)for(int j=0;j<BUS_BYTES;j++)begin
            longint unsigned at;
            at=at_byte(observed[i].address,beat)+64'(j);
            if(at<end_byte(observed[i].address,beat))data[int'(at%64'(BUS_BYTES))*8+:8]=golden[16'(at)];
          end
          if(observed_r[i][beat].id!=observed[i].address.id||observed_r[i][beat].last!=(beat==int'(observed[i].address.len))
            ||observed_r[i][beat].response!=response||observed_r[i][beat].data!==data)$fatal(1,"serial source oracle read mismatch transaction=%0d beat=%0d",i,beat);
        end
      end
    end
    reference_cursor=accepted;
    for(int i=0;i<65536;i++)if(memory[i]!==golden[i])$fatal(1,"unexpected byte effect at=%0d",i);
    if(count_family)begin tests++;$display("PASS lease %s",name);end
    else $display("CHECK lease %s",name);
  endtask
  task automatic publish;
    while(!cfg_ready)@(negedge clk);
    cfg_valid=1;@(negedge clk);cfg_valid=0;
    if(!config_valid)$fatal(1,"configuration rejected");
    while(!rearm_ready)@(negedge clk);
    rearm=1;@(negedge clk);rearm=0;
    while(!initialized)@(negedge clk);
    for(int p=0;p<2;p++)begin source_next[p]=0;import_next[p]=0;end
    enable=1;
  endtask
  task automatic measure_stream(input bit collision,input int count,id_base);
    int second_base;
    logic [63:0] start_grant_cycles;
    second_base=collision?'h2000:'h1040;
    measure_case=1;measure_active=0;measure_done=0;case_completed=0;
    window_warmup=count/2;window_completions=count;measure_bytes=0;
    measure_start_ps=0;measure_end_ps=0;service_delay=0;start_grant_cycles=grant_cycles;
    for(int p=0;p<2;p++)begin
      measure_cycles[p]=0;measure_awvalid[p]=0;measure_awready[p]=0;measure_awhandshakes[p]=0;
      measure_wvalid[p]=0;measure_wready[p]=0;measure_whandshakes[p]=0;
    end
    fork
      begin stream_addresses(0,count,'h1000,id_base);end
      begin stream_addresses(1,count,second_base,id_base+count);end
      begin stream_data(0,count,id_base+13);end
      begin stream_data(1,count,id_base+71);end
    join
    wait_responses();wait_settled();measure_case=0;service_delay=3;
    if(!measure_done||case_completed!=2*count||measure_bytes!=64'(window_completions)*64
      ||measure_end_ps<=measure_start_ps)$fatal(1,"incomplete completed-payload measurement window");
    $display("LEASE_MEASUREMENT collision=%0b writes=%0d bytes=%0d elapsed_ps=%0d payload_GBps=%0f warmup=%0d measured=%0d drain=%0d grant_cycles=%0d",
      collision,case_completed,measure_bytes,measure_end_ps-measure_start_ps,
      real'(measure_bytes)*1000.0/real'(measure_end_ps-measure_start_ps),window_warmup,window_completions,
      2*count-window_warmup-window_completions,grant_cycles-start_grant_cycles);
    for(int p=0;p<2;p++)$display("LEASE_DUTY collision=%0b port=%0d cycles=%0d awvalid=%0d awready=%0d awhandshakes=%0d wvalid=%0d wready=%0d whandshakes=%0d awstalls=%0d wstalls=%0d",
      collision,p,measure_cycles[p],measure_awvalid[p],measure_awready[p],measure_awhandshakes[p],
      measure_wvalid[p],measure_wready[p],measure_whandshakes[p],
      measure_awvalid[p]-measure_awhandshakes[p],measure_wvalid[p]-measure_whandshakes[p]);
    check_reference(collision?"legal four-KiB-separated collision stream":"independent disjoint lease-hit stream");
  endtask
  task automatic recover_fresh;
    enable=0;wait_responses();wait_settled();
    @(negedge clk);recover=1;@(negedge clk);recover=0;
    cfg.boot_epoch++;cfg.config_epoch++;publish();
  endtask
  task automatic disable_pending_case;
    int first_pending;
    send_address(0,0,address(25,'h5040,1));check_reference("prepare disjoint rights for disable crossing",0);
    @(negedge clk);run_host=0;first_pending=accepted;
    send_address(0,0,address(26,'h6040,1));send_address(1,0,address(27,'h6000,1));
    if(accepted!=first_pending+2||observed[first_pending].import_ordinal>=0
      ||observed[first_pending+1].import_ordinal>=0)$fatal(1,"disable setup did not retain actual accepted descriptors");
    enable=0;repeat(12)source_neg(0);run_host=1;
    check_reference("disable preserves accepted descriptors across stopped host clock");
    for(int i=first_pending;i<accepted;i++)if(observed[i].import_ordinal<0)
      $fatal(1,"disable stranded accepted descriptor instead of settling it");
    enable=1;
  endtask
  task automatic stale_command_case;
    int before_accepted;
    logic [31:0] old_token;
    logic [63:0] old_rights;
    before_accepted=accepted;
    source_neg(0);ar[0]=address(210,'h1180,1);arvalid[0]=1;
    do source_neg(0);while(!dut.bridge.ports[0].endpoint.lease_local_valid
      ||!dut.bridge.ports[0].endpoint.lease_local_ready);
    if(dut.bridge.ports[0].endpoint.lease_local.revoke)$fatal(1,"stale command setup did not select a grant");
    old_token=dut.bridge.ports[0].endpoint.lease_token_q;
    old_rights=dut.bridge.ports[0].endpoint.owned_mask_q;
    inject_command=1;
    force dut.bridge.ports[0].endpoint.lease_local.boot_epoch=32'hffffffff;
    #0.001;
    if(dut.bridge.ports[0].endpoint.lease_local.boot_epoch==cfg.boot_epoch)
      $fatal(1,"stale command injection did not reach actual sampled payload");
    source_pos(0);#0.001;
    if(!dut.bridge.ports[0].endpoint.fault_q
      ||dut.bridge.ports[0].endpoint.lease_token_q!=old_token
      ||dut.bridge.ports[0].endpoint.owned_mask_q!=old_rights
      ||dut.bridge.ports[0].endpoint.lease_ack_pending_q)
      $fatal(1,"stale command changed rights, token or acknowledgement");
    source_neg(0);release dut.bridge.ports[0].endpoint.lease_local.boot_epoch;
    inject_command=0;arvalid[0]=0;enable=0;
    while(!fault)@(negedge clk);
    check_reference("stale control epoch faults without granting source rights");
    if(accepted!=before_accepted)$fatal(1,"stale grant admitted the pending source address");
    recover_fresh();
  endtask
  task automatic stale_ack_case;
    logic [63:0] old_rights;
    service_delay=30;
    source_neg(0);ar[0]=address(211,'h1180,1);arvalid[0]=1;rready[0]=0;
    fork
      begin
        do source_pos(0);while(!arready[0]&&!fault);
        source_neg(0);arvalid[0]=0;
      end
      begin
        do @(negedge clk);while(!dut.bridge.lease_ack_valid[0]||!dut.bridge.lease_ack_ready[0]);
        old_rights=dut.bridge.owner_mask_q[0];inject_ack=1;
        force dut.bridge.lease_acks[0].command.token=32'h0;
        #0.001;
        if(dut.bridge.lease_acks[0].command==dut.bridge.expected_command_q[0])
          $fatal(1,"stale ACK injection did not reach actual sampled payload");
        @(posedge clk);#0.001;
        if(!dut.bridge.protocol_fault_q||dut.bridge.ack_received_q[0]
          ||dut.bridge.owner_mask_q[0]!=old_rights)
          $fatal(1,"stale ACK advanced acknowledgement or ownership");
        @(negedge clk);release dut.bridge.lease_acks[0].command.token;inject_ack=0;
      end
    join
    while(!fault)@(negedge clk);
    source_neg(0);rready[0]=1;enable=0;
    check_reference("stale acknowledgement token faults without ownership reassignment");
    service_delay=3;recover_fresh();
  endtask
  initial begin:test
    lpddr5_mc_axi_address_t a,d;
    int early,later,old_write,new_read,before_requests,aborted_imports;
    corrupt_read=$test$plusargs("LEASE_CORRUPT_READ");corrupt_memory=$test$plusargs("LEASE_CORRUPT_MEMORY");
    for(int i=0;i<MAX_TRANSACTIONS;i++)observed[i]='0;
    aw='0;ar='0;wd='0;cfg='0;completion='0;pending='0;pending_boot=0;pending_config=0;
    for(int i=0;i<65536;i++)begin memory[i]=8'((i*29+7)^(i>>8));golden[i]=memory[i];end
    for(int p=0;p<2;p++)begin source_next[p]=0;import_next[p]=0;end
    cfg.boot_epoch=1;cfg.config_epoch=1;cfg.granule64=1;cfg.read_limit='{default:16};cfg.write_limit='{default:16};
    cfg.regions[0].enable=1;cfg.regions[0].size=65536;
    for(int i=0;i<40;i++)cfg.regions[0].permutation[i]=6'(i);
    #20;rst_n=1;axi_rst_n=3;publish();stage=1;
    send_address(0,0,address(1,'h1000,1));send_address(1,0,address(2,'h1040,1));check_reference("disjoint lease warmup");
    @(negedge clk);run_host=0;
    send_address(1,0,address(3,'h2040,4));early=transaction(1,3,0);
    a=address(4,'h2000,4);send_address(0,1,a);later=transaction(0,4,1);send_write(0,a,81);
    if(early<0||later<=early||observed[early].accepted_at_ps>=observed[later].accepted_at_ps)$fatal(1,"source reversal setup did not accept port1 first");
    run_host=1;check_reference("source-local lease hit survives stopped host clock");
    if(observed[later].import_ordinal>=observed[early].import_ordinal)$fatal(1,"test did not produce decisive disjoint import reversal");
    $display("LEASE_REVERSAL accepted=%0d/%0d imported=%0d/%0d",early,later,observed[early].import_ordinal,observed[later].import_ordinal);stage=2;
    @(negedge clk);run_host=0;a=address(5,'h3000,4);send_address(0,1,a);old_write=transaction(0,5,1);
    before_requests=requests;
    fork
      begin send_address(1,0,address(6,'h3000,4));end
      begin
        repeat(12)@(negedge clk0);
        if(transaction(1,6,0)>=0||observed[old_write].import_ordinal>=0)$fatal(1,"conflicting lease bypassed accepted but unimported AW");
        run_host=1;
        repeat(30)@(negedge clk0);
        if(transaction(1,6,0)>=0||requests!=before_requests)$fatal(1,"conflicting lease released before delayed W");
        repeat(30)@(negedge clk0);send_write(0,a,117);
      end
    join
    new_read=transaction(1,6,0);
    if(new_read<0||observed[old_write].b_seen!=1
      ||observed[new_read].accepted_at_ps<=observed[old_write].b_handshake_at_ps)$fatal(1,"lease handover preceded old source handshake settlement");
    check_reference("conflicting handover includes unimported AW and independently delayed W");
    stage=3;
    a=address(7,'h43f,5,2,1);send_address(0,1,a);send_write(0,a,45,0,-1,1);
    d=a;d.id=8;send_address(1,0,d);check_reference("unaligned partial INCR crosses bucket boundary");
    a=address(9,'h4b0,16,4,2);send_address(1,1,a);send_write(1,a,63);
    d=a;d.id=10;send_address(0,0,d);check_reference("WRAP complete multibucket footprint");
    a=address(11,'h607,3,1,0);send_address(0,1,a);send_write(0,a,151);
    d=a;d.id=12;send_address(1,0,d);check_reference("FIXED repeated-byte serial order");
    a=address(13,'h800,4);send_address(0,1,a);send_write(0,a,190,0,-1,2);check_reference("malformed write no byte effects");
    before_requests=requests;send_address(1,0,address(14,'hff0,2));check_reference("invalid all-mask read complete DECERR");
    if(requests!=before_requests)$fatal(1,"invalid request produced channel effects");
    stage=4;
    send_address(0,0,address(15,'h1000,1));check_reference("prepare owner clock stop",0);
    source_neg(0);run0=0;
    fork
      begin send_address(1,0,address(16,'h1000,1));end
      begin
        repeat(80)@(negedge clk);
        if(transaction(1,16,0)>=0)$fatal(1,"stopped owner acknowledged revoke/reassignment");
        run0=1;
      end
    join
    check_reference("stopped owner clock blocks conflicting reassignment");
    rready[1]=0;send_address(1,0,address(17,'h1000,1));
    while(!rvalid[1])source_neg(1);
    fork
      begin a=address(18,'h1000,4);send_address(0,1,a);send_write(0,a,219);end
      begin
        // Offer another owner hit precisely while the real revoke FIFO head is exposed.
        do source_neg(1);while(!dut.bridge.ports[1].endpoint.lease_local_valid
          ||!dut.bridge.ports[1].endpoint.lease_local.revoke);
        ar[1]=address(24,'h1000,1);arvalid[1]=1;
        do source_pos(1);while(!arready[1]);
        source_neg(1);arvalid[1]=0;
      end
      begin
        repeat(80)@(negedge clk);
        if(transaction(0,18,1)>=0||!rvalid[1])$fatal(1,"revocation ACK ignored stalled source response");
        source_neg(1);rready[1]=1;
      end
    join
    check_reference("delayed revoke ACK retains response ownership through actual handshake");
    if(cutoff_samples==0)$fatal(1,"no offered address sampled on actual revoke-head cutoff");
    send_address(0,0,address(20,'h1000,1));send_address(1,0,address(19,'h1040,1));
    check_reference("prepare disjoint leases before fatal",0);
    stage=5;
    @(negedge clk);run_host=0;before_requests=requests;bready=0;rready=0;
    a=address(21,'h5000,4);send_address(0,1,a);send_write(0,a,244,0,1);
    send_address(1,0,address(22,'h5040,3));fatal=1;
    send_write(0,a,244,2);
    while(!bvalid[0])source_neg(0);
    while(!rvalid[1])source_neg(1);
    if(b[0].response!=2||r[1].response!=2||r[1].data!=0)$fatal(1,"fatal unimported work did not settle with errors");
    repeat(20)source_neg(0);
    if(!bvalid[0]||!rvalid[1]||requests!=before_requests)$fatal(1,"fatal lost held response or invented memory effect");
    bready=3;rready=3;run_host=1;enable=0;
    check_reference("fatal retains backpressured responses and settles unimported accepted work");
    stage=6;
    fatal=0;@(negedge clk);recover=1;@(negedge clk);recover=0;
    cfg.config_epoch++;
    while(!cfg_ready)@(negedge clk);
    cfg_valid=1;@(negedge clk);cfg_valid=0;repeat(4)@(negedge clk);
    if(config_valid||!fault||awready!=0||arready!=0)$fatal(1,"stale boot/configuration reopened lease admission");
    wait_settled();@(negedge clk);recover=1;@(negedge clk);recover=0;
    cfg.boot_epoch++;cfg.config_epoch++;publish();
    send_address(1,0,address(23,'h5000,1));check_reference("stale epoch cannot restore rights and fresh boot resumes service");
    stage=7;disable_pending_case();measure_stream(0,24,32);measure_stream(1,32,128);
    stage=8;stale_command_case();stale_ack_case();
    send_address(0,0,address(212,'h1180,1));check_reference("fresh epoch restores service after rejected control identities");
    aborted_imports=0;
    for(int i=0;i<accepted;i++)if(observed[i].import_ordinal<0)begin
      if(!observed[i].fatal_seen)$fatal(1,"accepted record vanished without import or fatal settlement");
      aborted_imports++;
    end
    if(tests!=18||imported+aborted_imports!=accepted||command_samples==0||ack_samples==0)
      $fatal(1,"lease accounting/family count mismatch");
    $display("LEASE_RESULT tests=%0d source=%0d imports=%0d aborted=%0d cutoff=%0d commands=%0d acks=%0d reversal=1",
      tests,accepted,imported,aborted_imports,cutoff_samples,command_samples,ack_samples);
    $finish;
  end
  initial begin #1000000;$fatal(1,"lease timeout stage=%0d fault=%b initialized=%b settled=%b accepted=%0d imports=%0d",stage,fault,initialized,settled,accepted,imported);end
endmodule
