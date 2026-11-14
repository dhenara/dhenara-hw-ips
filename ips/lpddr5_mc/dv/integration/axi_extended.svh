// Directed maximum-length and unsupported-exclusive traffic. Only source buses
// select outcomes; connected.py independently derives effects from CA/data/return.
function automatic byte unsigned extended_byte(input longint unsigned at,input byte unsigned salt);
  // Fold upper address bytes so 256-byte-offset assembly errors cannot hide
  // behind a repeating low-byte pattern within the 4096-byte transfer.
  return 8'((at*29)^(at>>8)^(at>>16)^64'(salt));
endfunction
task automatic extended_write(input int p,input logic [15:0] id,input longint unsigned at,
  input int beats,input int error_kind,input byte unsigned salt,input bit locked=0);
  int before_b;
  lpddr5_mc_axi_write_t value;
  before_b=b_count[p];
  source_negative(p);bready[p]=0;aw[p]=address(id,at,beats);aw[p].lock=locked;awvalid[p]=1;
  do source_positive(p);while(!awready[p]);source_negative(p);awvalid[p]=0;
  repeat(4) source_negative(p);
  for(int beat=0;beat<beats;beat++) begin
    value='0;value.last=beat==beats-1;
    if(error_kind==1) value.last=beat==0 || beat==beats-1;
    if(error_kind==2) value.last=0;
    for(int lane=0;lane<BYTES;lane++) begin
      value.data[lane*8+:8]=extended_byte(at+64'(beat)*64'(BYTES)+64'(lane),salt);
      value.strobe[lane]=1;
      if(error_kind==0) expected[at+64'(beat)*64'(BYTES)+64'(lane)]=value.data[lane*8+:8];
    end
    wd[p]=value;wvalid[p]=1;
    do source_positive(p);while(!wready[p]);source_negative(p);wvalid[p]=0;
  end
  while(!bvalid[p]) source_negative(p);
  repeat(8) source_negative(p);
  bready[p]=1;wait_b(p,id,before_b+1,2'(error_kind==3 ? 3 : error_kind==0 ? 0 : 2));
  wait_settled();
endtask

task automatic extended_read(input int p,input logic [15:0] id,input longint unsigned at,
  input int beats,input logic [1:0] response=0,input bit locked=0);
  source_negative(p);rready[p]=0;ar[p]=address(id,at,beats);ar[p].lock=locked;arvalid[p]=1;
  do source_positive(p);while(!arready[p]);source_negative(p);arvalid[p]=0;
  for(int beat=0;beat<beats;beat++) begin
    while(!rvalid[p]) source_negative(p);
    // Include first, interior and final beats of the maximum-length response.
    if(beat%64==0 || beat==beats-1) repeat(8) source_negative(p);
    rready[p]=1;do source_positive(p);while(!rvalid[p]);
    check(r[p].id==id && r[p].response==response && r[p].last==(beat==beats-1),"extended R identity/status/count");
    for(int lane=0;lane<BYTES;lane++)
      check(r[p].data[lane*8+:8]==(response==0 ? expected[at+64'(beat)*64'(BYTES)+64'(lane)] : 8'd0),"extended readback byte");
    source_negative(p);rready[p]=0;
  end
  wait_settled();
endtask

task automatic axi_extended_case;
  longint unsigned base,exclusive_at;
  check(PORTS==2 && CHANNELS==2 && RANKS==1 && DATA_BITS==128 && DEPTH==16
    && ID_BITS==8 && ADDRESS_BITS==40 && GRANULE_BYTES==64
    && PROFILE_NUMBER==0 && SERVICE_DIV==2 && INTEGRITY_MODE==0,"extended AXI fixed binding");
  base=64'd131072;exclusive_at=base+64'd16384;stage=300;
  // Exactly 4096 aligned bytes is legal; all 256 W/R beats must settle.
  extended_write(0,80,base,256,0,8'h11);
  extended_read(1,81,base,256);
  stage=301;extended_write(0,82,base,256,1,8'ha5);extended_read(1,83,base,256);
  stage=302;extended_write(1,84,base,256,2,8'h5a);extended_read(0,85,base,256);
  // Shift the same maximum descriptor by one granule: reject the entire burst,
  // consume every W, return 256 DECERR R beats, then reread the valid prefix.
  stage=303;extended_write(0,86,base+64,256,3,8'hff);
  extended_read(1,87,base+64,256,3);extended_read(1,88,base,256);
  // No exclusive monitor: locked writes have ordinary effects, every locked
  // read/write returns OKAY, and an intervening ordinary write changes nothing
  // about the following locked write's ordinary success/effect contract.
  stage=304;extended_write(1,89,exclusive_at,4,0,8'h22,1);
  extended_read(0,90,exclusive_at,4,0,1);
  extended_write(0,91,exclusive_at,4,0,8'h33);
  extended_write(1,92,exclusive_at,4,0,8'h44,1);
  extended_read(0,93,exclusive_at,4,0,1);extended_read(1,94,exclusive_at,4);
  extended_read(0,95,base,256);
  check(!host_fault && stale==0 && settled,"extended AXI clean final settlement");
  @(negedge clk);finish_observations=1;repeat(2) @(negedge clk);
  $display("DATAPATH_AXI_EXTENDED_PASS cases=16 max_beats=256");
  $display("DATAPATH_INTEGRATION_PASS checks=%0d axi_extended=1",checks);$finish;
endtask
