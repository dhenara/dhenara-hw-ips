module lpddr5_mc_contract_tb;
  import lpddr5_mc_contract_pkg::*;
  lpddr5_mc_granule_t granule;
  lpddr5_mc_error_t error_record;
  lpddr5_mc_read_bus4_t read_bus;
  lpddr5_mc_ca_t ca;

  initial begin
    granule = '0;
    granule.axi_id = 16'hffff;
    granule.address = 40'hffffffffff;
    granule.tag = 64'hffffffffffffffff;
    granule.byte_enable = 64'h8000000000000001;
    granule.data[511:504] = 8'hab;
    error_record = '0;
    error_record.axi_id = granule.axi_id;
    error_record.address = granule.address;
    error_record.tag = granule.tag;
    assert (error_record.axi_id == 16'hffff) else $fatal(1, "AXI ID truncated");
    assert (error_record.address == 40'hffffffffff) else $fatal(1, "address truncated");
    assert (error_record.tag == 64'hffffffffffffffff) else $fatal(1, "tag truncated");
    assert ($bits(lpddr5_mc_age_t) == 16) else $fatal(1, "age width");
    assert ($bits(granule.data) == 512) else $fatal(1, "granule width");
    assert ($bits(granule.byte_enable) == 64) else $fatal(1, "byte enable width");
    ca.address = {7'h55, 7'h2a};
    ca.cs = 2'b01;
    assert (ca.address[6:0] == 7'h2a && ca.address[13:7] == 7'h55)
      else $fatal(1, "CA edge mapping");
    read_bus = '0;
    read_bus.data[3] = 32'hdeadbeef;
    read_bus.valid[3] = 2'b10;
    assert ($bits(read_bus.data) == 128 && $bits(read_bus.valid) == 8)
      else $fatal(1, "DFI word/slice dimensions");
    assert (read_bus.data[3] == 32'hdeadbeef && read_bus.valid[3] == 2'b10)
      else $fatal(1, "word/slice indexing");
    $display("PASS draft contract representation smoke; no controller tested; checksums=%b%b%b",
             ^granule, ^error_record, ^ca);
    $finish;
  end
endmodule
