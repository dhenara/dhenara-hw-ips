module adapter_register_tb;
  import lpddr5_mc_apb_metadata_pkg::*;
  import lpddr5_mc_host_pkg::*;
  lpddr5_mc_register_image_t image;
  lpddr5_mc_host_config_t config_out, saved_config;
  logic [31:0] boot_epoch = 32'h12345678, config_epoch = 32'h89abcdef;
  int checks = 0;
  int vectors = 0;

  lpddr5_mc_host_register_decode dut (
    .image_i(image), .boot_epoch_i(boot_epoch), .config_epoch_i(config_epoch), .config_o(config_out)
  );

  task automatic check(input logic condition, input string explanation);
    assert(condition) else $fatal(1, "%s", explanation);
    checks++;
  endtask

  task automatic map_check(input logic [39:0] address, input int channels, input int ranks,
      input logic valid, input logic channel_index, input logic rank_index, input logic [3:0] bank,
      input logic [17:0] row, input logic [11:0] column, input logic [39:0] physical_address);
    lpddr5_mc_host_decode_t actual;
    actual = decode_address(config_out, address, channels, ranks);
    check(actual.valid == valid, $sformatf("decode validity at %h", address));
    if (valid) begin
      check(actual.channel_index == channel_index && actual.rank_index == rank_index &&
          actual.bank == bank && actual.row == row && actual.column == column &&
          actual.physical_address == physical_address, $sformatf("canonical decode mismatch at %h", address));
    end
    vectors++;
  endtask

  // Read oracle data at runtime so one bounded decoder loop serves every
  // vector. Hundreds of static task calls create very large simulator code.
  task automatic oracle_checks();
    string path;
    int stream, case_count, channels, ranks, vector_count;
    logic [39:0] address, physical_address;
    logic valid, channel_index, rank_index;
    logic [3:0] bank;
    logic [17:0] row;
    logic [11:0] column;
    check($value$plusargs("vectors=%s", path), "missing oracle vector file");
    stream = $fopen(path, "r");
    check(stream != 0, "cannot read oracle vector file");
    check($fscanf(stream, "%d", case_count) == 1 && case_count == 6, "oracle case header");
    for (int case_index = 0; case_index < case_count; case_index++) begin
      check($fscanf(stream, "%d %d %d", channels, ranks, vector_count) == 3, "oracle configuration header");
      for (int reg_index = 0; reg_index < REG_COUNT; reg_index++)
        check($fscanf(stream, "%h", image[reg_index]) == 1, "oracle image word");
      #1;
      check(configuration_valid(config_out, channels, ranks, 40), "canonical configuration rejected");
      for (int vector_index = 0; vector_index < vector_count; vector_index++) begin
        check($fscanf(stream, "%h %h %h %h %h %h %h %h", address, valid, channel_index,
            rank_index, bank, row, column, physical_address) == 8, "oracle decode vector");
        map_check(address, channels, ranks, valid, channel_index, rank_index, bank, row, column, physical_address);
      end
    end
    $fclose(stream);
  endtask

  initial begin
    for (int r = 0; r < REG_COUNT; r++) image[r] = 0;
    image[INDEX_QOS_MAP] = 32'he4e4e4e4;
    image[INDEX_MODE] = 32'h00000413;
    image[INDEX_PORT_LIMIT_0] = 32'hffff3010;
    image[INDEX_PORT_LIMIT_1] = 32'hffff4020;
    image[INDEX_BOOT_EPOCH] = 32'hffffffff;
    image[INDEX_CONFIG_EPOCH] = 0;
    image[INDEX_REGION_0_BASE_LO] = 32'hfedcb000;
    image[INDEX_REGION_0_BASE_HI] = 32'hf0f0f0ab;
    image[INDEX_REGION_0_SIZE_4K] = 32'hf0000001;
    image[INDEX_REGION_0_ATTR] = 32'hffffffff;
    for (int bit_index = 0; bit_index < 40; bit_index++)
      image[REGION_BIT_INDEX[0][bit_index]] = 32'hf0000000 | 32'(39 - bit_index);
    #1;
    check(config_out.qos_map == 32'he4e4e4e4, "all sixteen QoS classes retained");
    for (int q = 0; q < 16; q++) check(config_out.qos_map[q] == 2'(q % 4), "QoS lane order");
    check(config_out.read_limit[0] == 16 && config_out.write_limit[0] == 48 &&
        config_out.read_limit[1] == 32 && config_out.write_limit[1] == 64, "port credit fields");
    check(config_out.granule64 && config_out.inline_ecc && config_out.posted, "host mode fields");
    check(config_out.boot_epoch == boot_epoch && config_out.config_epoch == config_epoch,
        "external committed epochs were replaced by image diagnostics");
    check(config_out.regions[0].enable && config_out.regions[0].base == 40'habfedcb000 &&
        config_out.regions[0].size == 4096, "region widths and reserved bits");
    for (int bit_index = 0; bit_index < 40; bit_index++)
      check(config_out.regions[0].permutation[bit_index] == 6'(39 - bit_index), "complete permutation retained");
    saved_config = config_out;
    image[INDEX_ID] = 32'hbadbadba;
    image[INDEX_STATUS] = 32'hffffffff;
    image[INDEX_FSP0_TRCD] = 32'hff00ff00;
    #1;
    check(config_out == saved_config, "channel timing/status leaked into host configuration");
    image[INDEX_MODE] = 32'h00000100;
    boot_epoch = 32'hffffffff;
    config_epoch = 32'hffffffff;
    #1;
    check(!config_out.granule64 && !config_out.inline_ecc && !config_out.posted &&
        config_out.boot_epoch == 32'hffffffff && config_out.config_epoch == 32'hffffffff,
        "32-byte/strict/off options or epoch boundary");

    oracle_checks();
    $display("ADAPTER_REGISTER_PASS checks=%0d canonical_vectors=%0d seed=2095", checks, vectors);
    $finish;
  end
endmodule
