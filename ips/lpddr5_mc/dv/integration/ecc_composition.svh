// Focused real datapath composition. Never overrides channel readiness,
// completions, ECC outcomes, AXI leases or POSTED_EARLY.
int composition_stream=0,composition_requests=0,composition_completions=0;
bit composition_offer_seen=0;
logic [63:0] composition_last_offer=0;
string composition_path;

task automatic composition_request(input string phase_name);
  int owner,printed;
  lpddr5_mc_granule_t value;
  value=dut.requests[0];owner=int'(dut.host.core.request_owner_q[0]);
  $fwrite(composition_stream,"{\"kind\":\"request\",\"phase\":\"%s\",\"ps\":%0d,\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"write\":%0d,\"id\":%0d,\"address\":%0d,\"mask\":\"0x%h\",\"data\":\"0x%h\",\"overlay_mask\":\"0x%h\",\"overlay_data\":\"0x%h\",\"owners\":[",
    phase_name,observer.timestamp_ps(),value.tag,value.generation,dut.request_boot[0],dut.request_epoch[0],value.is_write,value.axi_id,value.address,value.byte_enable,value.data,
    dut.host.core.forward_mask[owner],dut.host.core.forward_data[owner]);
  printed=0;
  for(int s=0;s<PORTS*2*DEPTH;s++) begin
    if(dut.host.core.transactions[s].valid && dut.host.core.transactions[s].pending
      && dut.host.core.transactions[s].identity.tag==value.tag
      && dut.host.core.transactions[s].identity.generation==value.generation
      && dut.host.core.transactions[s].write_access==value.is_write) begin
      if(printed!=0)$fwrite(composition_stream,",");printed++;
      $fwrite(composition_stream,"{\"id\":%0d,\"slot\":%0d,\"generation\":%0d,\"order\":%0d,\"buffered\":%0d,\"validated\":%0d}",
        dut.host.core.transactions[s].address.id,dut.host.core.transactions[s].source_slot,
        dut.host.core.transactions[s].generation,dut.host.core.transactions[s].order_number,
        dut.host.core.transactions[s].fully_buffered,dut.host.core.transactions[s].validated);
    end
  end
  $fwrite(composition_stream,"],\"contributors\":[");printed=0;
  for(int s=0;s<PORTS*2*DEPTH;s++) if(!value.is_write && dut.host.core.dependencies[owner][s]) begin
    if(printed!=0)$fwrite(composition_stream,",");printed++;
    $fwrite(composition_stream,"{\"id\":%0d,\"slot\":%0d,\"generation\":%0d}",
      dut.host.core.transactions[s].address.id,dut.host.core.transactions[s].source_slot,dut.host.core.transactions[s].generation);
  end
  $fdisplay(composition_stream,"]}");
endtask

if(ECC_COMPOSITION!=0) begin : composition_observation
  initial begin
    check($value$plusargs("composition_observations=%s",composition_path),"composition trace path");
    composition_stream=$fopen(composition_path,"w");check(composition_stream!=0,"composition trace open");
    $fdisplay(composition_stream,"{\"kind\":\"header\",\"version\":1,\"ports\":1,\"channels\":1,\"bus_bytes\":16,\"inline_ecc\":1,\"posted_early\":0}");
  end
  always @(posedge clk or negedge rst_n) begin
    if(!rst_n) begin
      composition_requests<=0;composition_completions<=0;
      composition_last_offer<=0;composition_offer_seen<=0;
    end else if(composition_stream!=0) begin
    // A source AR is insufficient to prove that the core saw the hazard.
    // Sample both original generations while the youngest W is incomplete;
    // the checker also binds the AR/W edges and rejects any early read offer.
    for(int rslot=0;rslot<PORTS*2*DEPTH;rslot++) begin
      if(dut.host.core.transactions[rslot].valid && !dut.host.core.transactions[rslot].write_access
          && dut.host.core.transactions[rslot].fully_buffered && dut.host.core.transactions[rslot].validated)
        for(int wslot=0;wslot<PORTS*2*DEPTH;wslot++) begin
          if(dut.host.core.transactions[wslot].valid && dut.host.core.transactions[wslot].write_access
              && !dut.host.core.transactions[wslot].fully_buffered
              && dut.host.core.transactions[wslot].address.id+16'd1==dut.host.core.transactions[rslot].address.id)
            $fdisplay(composition_stream,"{\"kind\":\"hazard\",\"ps\":%0d,\"read_id\":%0d,\"writer_id\":%0d,\"read_generation\":%0d,\"writer_generation\":%0d,\"read_buffered\":%0d,\"read_validated\":%0d,\"read_pending\":%0d,\"read_offered\":%0d,\"blocked\":%0d,\"forwardable\":%0d}",
              observer.timestamp_ps(),dut.host.core.transactions[rslot].address.id,dut.host.core.transactions[wslot].address.id,
              dut.host.core.transactions[rslot].generation,dut.host.core.transactions[wslot].generation,
              dut.host.core.transactions[rslot].fully_buffered,dut.host.core.transactions[rslot].validated,
              dut.host.core.transactions[rslot].pending,
              dut.request_valid[0] && dut.requests[0].axi_id==dut.host.core.transactions[rslot].address.id,
              dut.host.core.blocked_by_older(rslot),dut.host.core.forwarding_allowed(rslot));
        end
    end
    // Imports carry source slot/generation; original source edges remain in
    // observations.jsonl and are correlated independently by ID and direction.
    if(dut.host.aw_valid[0] && dut.host.aw_ready[0])
      $fdisplay(composition_stream,"{\"kind\":\"import\",\"ps\":%0d,\"write\":1,\"id\":%0d,\"slot\":%0d,\"generation\":%0d,\"sequence\":%0d}",
        observer.timestamp_ps(),dut.host.aw[0].axi.id,dut.host.aw[0].slot,dut.host.aw[0].generation,dut.host.aw[0].sequence_number);
    if(dut.host.ar_valid[0] && dut.host.ar_ready[0])
      $fdisplay(composition_stream,"{\"kind\":\"import\",\"ps\":%0d,\"write\":0,\"id\":%0d,\"slot\":%0d,\"generation\":%0d,\"sequence\":%0d}",
        observer.timestamp_ps(),dut.host.ar[0].axi.id,dut.host.ar[0].slot,dut.host.ar[0].generation,dut.host.ar[0].sequence_number);
    if(dut.request_valid[0] && (!composition_offer_seen || composition_last_offer!=dut.requests[0].tag)) begin
      composition_request("offer");composition_last_offer<=dut.requests[0].tag;composition_offer_seen<=1;
    end
    if(dut.request_valid[0] && dut.request_ready[0]) begin
      composition_request("take");composition_requests<=composition_requests+1;
    end
    if(dut.channels[0].channel.inline_physical_valid && dut.channels[0].channel.inline_physical_ready)
      $fdisplay(composition_stream,"{\"kind\":\"physical\",\"ps\":%0d,\"owner\":%0d,\"owner_generation\":%0d,\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"write\":%0d,\"address\":%0d,\"mask\":\"0x%h\",\"data\":\"0x%h\"}",
        observer.timestamp_ps(),dut.channels[0].channel.inline_ecc.identity_q.tag,dut.channels[0].channel.inline_ecc.identity_q.generation,
        dut.channels[0].channel.inline_physical.tag,dut.channels[0].channel.inline_physical.generation,
        dut.request_boot[0],dut.request_epoch[0],dut.channels[0].channel.inline_physical.is_write,
        {1'b0,dut.channels[0].channel.inline_physical.row[14:0],dut.channels[0].channel.inline_physical.bank_group,dut.channels[0].channel.inline_physical.bank,dut.channels[0].channel.inline_physical.column[5:0],5'b0},
        dut.channels[0].channel.inline_physical.byte_enable,dut.channels[0].channel.inline_physical.data);
    if(dut.channels[0].channel.backend_completion_valid && dut.channels[0].channel.inline_result_ready)
      $fdisplay(composition_stream,"{\"kind\":\"result\",\"ps\":%0d,\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"write\":%0d,\"physical\":%0d,\"integrity\":%0d,\"failed\":%0d,\"data\":\"0x%h\"}",
        observer.timestamp_ps(),dut.channels[0].channel.backend_completion.identity.tag,dut.channels[0].channel.backend_completion.identity.generation,
        dut.channels[0].channel.backend_completion.identity.boot_epoch,dut.channels[0].channel.backend_completion.identity.config_epoch,
        dut.channels[0].channel.backend_completion.is_write,dut.channels[0].channel.backend_completion.physical_done,dut.channels[0].channel.backend_completion.integrity_done,
        dut.channels[0].channel.backend_completion.failed,dut.channels[0].channel.backend_completion.data);
    if(dut.completion_valid[0] && dut.completion_ready[0]) begin
      $fdisplay(composition_stream,"{\"kind\":\"complete\",\"ps\":%0d,\"tag\":%0d,\"generation\":%0d,\"boot\":%0d,\"config\":%0d,\"write\":%0d,\"physical\":%0d,\"integrity\":%0d,\"failed\":%0d,\"response\":%0d,\"data\":\"0x%h\"}",
        observer.timestamp_ps(),dut.channel_completions[0].identity.tag,dut.channel_completions[0].identity.generation,
        dut.channel_completions[0].identity.boot_epoch,dut.channel_completions[0].identity.config_epoch,
        dut.channel_completions[0].is_write,dut.channel_completions[0].physical_done,dut.channel_completions[0].integrity_done,
        dut.channel_completions[0].failed,dut.channel_completions[0].response,dut.channel_completions[0].data);
      composition_completions<=composition_completions+1;
    end
    end
  end
end

task automatic composition_w(input longint unsigned at,input logic [15:0] stencil,input byte unsigned salt,input int first=0,last=4);
  lpddr5_mc_axi_write_t value;
  for(int beat=first;beat<last;beat++) begin
    value='0;value.last=beat==3;value.strobe[15:0]=stencil;
    for(int lane=0;lane<16;lane++) begin
      value.data[lane*8+:8]=8'(at+64'(beat)*64'd16+64'(lane))^salt;
      if(stencil[lane])expected[at+64'(beat)*64'd16+64'(lane)]=value.data[lane*8+:8];
    end
    source_negative(0);wd[0]=value;wvalid[0]=1;
    do source_positive(0);while(!wready[0]);source_negative(0);wvalid[0]=0;
  end
endtask

task automatic composition_case(input int which);
  int request_base,completion_base,response_base;
  longint unsigned at;
  logic [15:0] base_id;
  at=which==0 ? 64'h4000 : 64'h8000;base_id=16'(160+which*8);
  request_base=composition_requests;completion_base=composition_completions;response_base=b_count[0];
  bready[0]=0;
  send_aw(0,base_id,at,4);send_aw(0,base_id+16'd1,at,4);send_aw(0,base_id+16'd2,at,4);
  composition_w(at,16'hffff,8'h15);
  while(composition_requests<request_base+1)@(negedge clk);
  composition_w(at,16'h5555,8'h53);composition_w(at,16'h00ff,8'h9b,0,3);
  send_ar(0,base_id+16'd3,at,4);
  // The youngest writer remains incomplete after the overlapping AR. The
  // independent checker rejects any early offered or accepted read request.
  repeat(8)@(negedge clk);
  composition_w(at,16'h00ff,8'h9b,3,4);
  if(which==1) begin
    while(composition_completions<completion_base+2)@(negedge clk);
    @(negedge clk);
    // Corrupt an untouched byte in a partially updated protected word after
    // the old physical read, before the contributors' RMW. No result is forced.
    memories[0].memory_model.memory_bytes[at+11]^=8'hc0;
    $fdisplay(composition_stream,"{\"kind\":\"flip\",\"ps\":%0d,\"address\":%0d,\"xor\":192}",observer.timestamp_ps(),at+11);
  end
  while(composition_completions<completion_base+3)@(negedge clk);
  check(composition_requests==request_base+3,"three logical operations for four original transactions");
  // Hold both response channels after final integrity as a stability probe.
  repeat(12)source_negative(0);bready[0]=1;
  read_result(0,base_id+16'd3,at,4,which==0 ? 2'd0 : 2'd2);
  while(b_count[0]<response_base+3)source_negative(0);
  wait_settled();
endtask

task automatic ecc_composition_case;
  check(PORTS==1 && CHANNELS==1 && RANKS==1 && DATA_BITS==128 && INTEGRITY_MODE==1,"ECC composition shape");
  composition_case(0);composition_case(1);
  check(!host_fault && stale==0 && !status_value[0].fault,"composition correlated failures retain ownership");
  @(negedge clk);enable=0;send_command(CH_CMD_QUIESCE);
  for(int n=0;n<3000;n++) begin @(negedge clk);if(status_value[0].state==1 && status_value[0].drained)break;end
  check(status_value[0].state==1 && status_value[0].drained,"composition channel drained");
  @(negedge clk);
  $fdisplay(composition_stream,"{\"kind\":\"end\",\"ps\":%0d,\"settled\":%0d,\"host_fault\":%0d,\"channel_fault\":%0d,\"stale\":%0d,\"late_error\":%0d,\"state\":%0d,\"drained\":%0d,\"pin_drained\":%0d,\"ecc_drained\":%0d}",
    observer.timestamp_ps(),settled,host_fault,status_value[0].fault,stale,late_error,status_value[0].state,status_value[0].drained,status_value[0].pin_drained,status_value[0].ecc_drained);
  $fclose(composition_stream);composition_stream=0;
  finish_observations=1;repeat(2)@(negedge clk);
  $display("DATAPATH_ECC_COMPOSITION_PASS cases=2 original_writes=6 original_reads=2 logical_writes=4 logical_reads=2");
  $display("DATAPATH_INTEGRATION_PASS checks=%0d integrity=%0d",checks,INTEGRITY_MODE);$finish;
endtask
