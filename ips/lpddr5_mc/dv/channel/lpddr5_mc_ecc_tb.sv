module lpddr5_mc_ecc_tb;
  import lpddr5_mc_ecc_pkg::*;
  logic [127:0] data;
  logic [8:0] link_checks;
  logic [7:0] inline_checks;
  logic [15:0] dmi_data;
  logic [5:0] dmi_checks;
  logic [136:0] link_word, link_fault;
  logic [71:0] inline_word, inline_fault;
  logic [21:0] dmi_word, dmi_fault;
  inline_result_t ir;
  link_result_t lr;
  dmi_result_t dr;
  string path;
  int fd, scanned, count;
  initial begin
    if (!$value$plusargs("vectors=%s",path)) $fatal(1,"missing vectors");
    fd=$fopen({path,"/ecc.txt"},"r"); count=0;
    assert(fd!=0) else $fatal(1,"open ECC vectors");
    while (!$feof(fd)) begin
      scanned=$fscanf(fd,"%h %h %h %h %h\n",data,link_checks,inline_checks,dmi_data,dmi_checks);
      assert(scanned==5) else $fatal(1,"parse ECC vectors");
      assert(link_encode(data)==link_checks && inline_encode(data[63:0])==inline_checks && dmi_encode(dmi_data)==dmi_checks) else $fatal(1,"independent encoding");
      link_word={link_checks,data}; inline_word={inline_checks,data[63:0]}; dmi_word={dmi_checks,dmi_data};
      ir=inline_decode(data[63:0],inline_checks); lr=link_decode(data,link_checks); dr=dmi_decode(dmi_data,dmi_checks);
      assert(ir.syndrome==0 && !ir.corrected && !ir.uncorrectable && ir.data==data[63:0]) else $fatal(1,"clean inline");
      assert(lr.syndrome==0 && !lr.corrected && !lr.uncorrectable && lr.data==data) else $fatal(1,"clean link");
      assert(dr.syndrome==0 && !dr.corrected && !dr.uncorrectable && dr.data==dmi_data) else $fatal(1,"clean DMI");
      for (int i=0;i<137;i++) begin
        link_fault=link_word^(137'b1<<i); lr=link_decode(link_fault[127:0],link_fault[136:128]);
        assert(lr.corrected && !lr.uncorrectable && lr.data==data) else $fatal(1,"link single %0d",i);
        for (int j=i+1;j<137;j++) begin
          link_fault=link_word^(137'b1<<i)^(137'b1<<j); lr=link_decode(link_fault[127:0],link_fault[136:128]);
          assert(lr.uncorrectable && !lr.corrected) else $fatal(1,"link double %0d,%0d",i,j);
        end
      end
      for (int i=0;i<72;i++) begin
        inline_fault=inline_word^(72'b1<<i); ir=inline_decode(inline_fault[63:0],inline_fault[71:64]);
        assert(ir.corrected && !ir.uncorrectable && ir.data==data[63:0]) else $fatal(1,"inline single %0d",i);
        for (int j=i+1;j<72;j++) begin
          inline_fault=inline_word^(72'b1<<i)^(72'b1<<j); ir=inline_decode(inline_fault[63:0],inline_fault[71:64]);
          assert(ir.uncorrectable && !ir.corrected) else $fatal(1,"inline double %0d,%0d",i,j);
        end
      end
      for (int i=0;i<22;i++) begin
        dmi_fault=dmi_word^(22'b1<<i); dr=dmi_decode(dmi_fault[15:0],dmi_fault[21:16]);
        assert(dr.corrected && !dr.uncorrectable && dr.data==dmi_data) else $fatal(1,"DMI single");
        for (int j=i+1;j<22;j++) begin
          dmi_fault=dmi_word^(22'b1<<i)^(22'b1<<j); dr=dmi_decode(dmi_fault[15:0],dmi_fault[21:16]);
          assert(dr.uncorrectable && !dr.corrected) else $fatal(1,"DMI double");
        end
      end
      count++;
    end
    assert(count==8) else $fatal(1,"missing ECC payloads");
    $display("PASS ECC: eight independent payloads, every single/double inline72/link137/DMI22 fault");
    $finish;
  end
endmodule
