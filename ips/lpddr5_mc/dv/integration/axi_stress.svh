// Seeded source traffic through the real host/CDC/channel datapath. Address
// calculations here follow AXI beat rules independently of the RTL helpers;
// connected.py separately checks sampled source and external pin effects.
int stress_seed=0;
bit [31:0] stress_rng;
function automatic int unsigned stress_random();
  stress_rng^=stress_rng<<13;stress_rng^=stress_rng>>17;stress_rng^=stress_rng<<5;
  return stress_rng;
endfunction
function automatic longint unsigned stress_start(input lpddr5_mc_axi_address_t a,input int beat);
  longint unsigned width_value,aligned,span,wrap_base;
  width_value=64'd1<<a.size;aligned=64'(a.address)&~(width_value-1);
  span=width_value*(64'(a.len)+1);wrap_base=aligned/span*span;
  if(a.burst==2) return wrap_base+(64'(a.address)-wrap_base+64'(beat)*width_value)%span;
  return beat==0 || a.burst==0 ? 64'(a.address) : aligned+64'(beat)*width_value;
endfunction
function automatic longint unsigned stress_end(input lpddr5_mc_axi_address_t a,input int beat);
  longint unsigned width_value;
  width_value=64'd1<<a.size;
  return (stress_start(a,beat)&~(width_value-1))+width_value;
endfunction
task automatic stress_write(input int p,input lpddr5_mc_axi_address_t a,input int error_kind);
  int before_b;
  longint unsigned first_byte,last_byte;
  lpddr5_mc_axi_write_t value;
  before_b=b_count[p];
  source_negative(p);bready[p]=0;aw[p]=a;awvalid[p]=1;
  do source_positive(p);while(!awready[p]);source_negative(p);awvalid[p]=0;
  // AW remains an ordering obligation during this real source-side W delay.
  repeat(2+int'(stress_random()%7)) source_negative(p);
  for(int beat=0;beat<=int'(a.len);beat++) begin
    first_byte=stress_start(a,beat);last_byte=stress_end(a,beat);value='0;
    value.last=beat==int'(a.len);
    if(error_kind==2) value.last=beat==0 || beat==int'(a.len);
    if(error_kind==3) value.last=0;
    for(int lane=0;lane<BYTES;lane++) value.data[lane*8+:8]=8'(stress_random());
    for(longint unsigned at=first_byte;at<last_byte;at++) begin
      int lane;
      lane=int'(at%64'(BYTES));
      value.strobe[lane]=at==first_byte || (beat+lane)%3!=0;
      if(error_kind==0 && value.strobe[lane]) expected[at]=value.data[lane*8+:8];
    end
    if(error_kind==1 && beat==0) value.strobe[(int'(first_byte%64'(BYTES))+1)%BYTES]=1;
    wd[p]=value;wvalid[p]=1;do source_positive(p);while(!wready[p]);source_negative(p);wvalid[p]=0;
    repeat(int'(stress_random()%3)) source_negative(p);
  end
  // Wait for an actual offered response before stalling it. A fixed source
  // delay alone would not demonstrate exposed-B stability under backpressure.
  while(!bvalid[p]) source_negative(p);
  repeat(5+int'(stress_random()%4)) source_negative(p);
  bready[p]=1;wait_b(p,a.id,before_b+1,2'(error_kind==4 ? 3 : error_kind==0 ? 0 : 2));
  wait_settled();
endtask
task automatic stress_read(input int p,input lpddr5_mc_axi_address_t a,input logic [1:0] response);
  logic [255:0] wanted;
  longint unsigned first_byte,last_byte;
  source_negative(p);rready[p]=0;ar[p]=a;arvalid[p]=1;
  do source_positive(p);while(!arready[p]);source_negative(p);arvalid[p]=0;
  while(!rvalid[p]) source_negative(p);
  repeat(3+int'(stress_random()%5)) source_negative(p);
  for(int beat=0;beat<=int'(a.len);beat++) begin
    wanted='0;first_byte=stress_start(a,beat);last_byte=stress_end(a,beat);
    if(response==0) for(longint unsigned at=first_byte;at<last_byte;at++)
      if(expected.exists(at)!=0) wanted[int'(at%64'(BYTES))*8+:8]=expected[at];
    rready[p]=1;do source_positive(p);while(!rvalid[p]);
    check(r[p].id==a.id && r[p].response==response && r[p].last==(beat==int'(a.len)),"stress R identity/status/count");
    check(r[p].data[DATA_BITS-1:0]==wanted[DATA_BITS-1:0],"stress independent byte lanes");
    source_negative(p);rready[p]=0;
    repeat(int'(stress_random()%4)) source_negative(p);
  end
  wait_settled();
endtask
task automatic axi_stress_case;
  lpddr5_mc_axi_address_t a,clean;
  longint unsigned base,width_value;
  int error_kind,beats,p;
  check($value$plusargs("axi_stress_seed=%d",stress_seed) && stress_seed>0,"explicit positive AXI stress seed");
  stress_rng=32'(stress_seed);
  for(int n=0;n<64;n++) begin
    p=n%PORTS;stage=100+n;error_kind=n%16>=12 ? n%16-11 : 0;
    base=64'd131072+64'(stress_random()%32)*4096+64'(stress_random()%8)*128;
    beats=1<<(1+(n/3)%4);a=address(16'(n%4),base,beats);
    a.burst=2'(n%3);a.size=3'((n/3)%($clog2(BYTES)+1));
    if(error_kind==1) a.size=0; // Guarantees an out-of-range strobe lane.
    width_value=64'd1<<a.size;
    if(a.burst==2) a.address=40'(base+width_value); // Aligned beat, wrapping span.
    else if(width_value>1) a.address=40'(base+1); // Unaligned first/fixed beat.
    if(error_kind==4) begin
      a.burst=1;a.size=3'($clog2(BYTES));a.len=1;
      a.address=40'((base&~64'd4095)+4096-64'(BYTES));
    end
    stress_write(p,a,error_kind);
    stress_read(PORTS-1-p,a,2'(error_kind==4 ? 3 : 0));
    if(error_kind==4) begin
      // A rejected crossing write must leave the valid last granule intact.
      clean=address(a.id,(64'(a.address)&~64'd4095)+4096-64,64/BYTES);
      stress_read(PORTS-1-p,clean,0);
    end
    check(!host_fault && stale==0,"stress service remains healthy after complete settlement");
  end
  @(negedge clk);finish_observations=1;repeat(2) @(negedge clk);
  $display("DATAPATH_AXI_STRESS_PASS seed=%0d cases=64",stress_seed);
  $display("DATAPATH_INTEGRATION_PASS checks=%0d axi_stress=%0d",checks,stress_seed);$finish;
endtask
