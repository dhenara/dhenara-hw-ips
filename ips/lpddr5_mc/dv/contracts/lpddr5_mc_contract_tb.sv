module lpddr5_mc_contract_tb;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_register_pkg::*;
  lpddr5_mc_parameters_t parameters;
  lpddr5_mc_axi_write_t axi_write;
  lpddr5_mc_return4_t raw_return;
  lpddr5_mc_wck4_t wck;

  lpddr5_mc_granule_t granule;
  lpddr5_mc_error_t error_record;
  lpddr5_mc_read_bus4_t read_bus;
  lpddr5_mc_ca_t ca;

  initial begin
    parameters = '{ports:2, channels:2, ranks:2, axi_bits:256, id_bits:16,
                   address_bits:40, queue_depth:64, service_div:2, wck_ratio:4,
                   read_link:1, write_link:1, raw_parity:1};
    assert (lpddr5_mc_parameters_valid(parameters)) else $fatal(1, "valid parameter rejection");
    parameters.raw_parity = 0;
    assert (!lpddr5_mc_parameters_valid(parameters)) else $fatal(1, "raw parity admission");
    parameters.raw_parity = 1;
    parameters.wck_ratio = 2;
    assert (!lpddr5_mc_parameters_valid(parameters)) else $fatal(1, "link ratio admission");
    parameters.wck_ratio = 4;
    parameters.axi_bits = 192;
    assert (!lpddr5_mc_parameters_valid(parameters)) else $fatal(1, "width truncation admission");
    assert (lpddr5_mc_dfi_ratio(4) == 2'b10 && lpddr5_mc_dfi_ratio(2) == 2'b01)
      else $fatal(1, "DFI ratio encoding");
    assert (LPDDR5_MC_CHANNEL_BASE == 'h10000 && LPDDR5_MC_CHANNEL_STRIDE == 'h10000)
      else $fatal(1, "channel address");
    assert (LPDDR5_MC_FSP0_RL == 'h1074 && LPDDR5_MC_MODE_SERVICE_DIV_MASK == 'h700)
      else $fatal(1, "generated register schema");
    axi_write = '0;
    axi_write.data[255:248] = 8'hfe;
    axi_write.strobe[31] = 1;
    axi_write.last = 1;
    raw_return = '0;
    raw_return.link_parity[3] = 4'ha;
    raw_return.dnv[3] = 4'h5;
    wck = '0;
    wck.toggle[3][1] = 2'b11;
    assert (axi_write.data[255:248] == 8'hfe && axi_write.strobe[31] && axi_write.last)
      else $fatal(1, "max AXI width");
    assert (raw_return.link_parity[3] == 4'ha && raw_return.dnv[3] == 4'h5 && wck.toggle[3][1] == 2'b11)
      else $fatal(1, "raw transport/WCK dimensions");
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
    $display("PASS canonical contract representation smoke; no controller tested; checksums=%b%b%b",
             ^granule ^ ^axi_write, ^error_record ^ ^raw_return, ^ca ^ ^wck);
    $finish;
  end
endmodule
