// Decode an already committed coherent register image. This combinational
// adapter neither validates a PHY profile nor authorizes publication/traffic.
module lpddr5_mc_host_register_decode (
  input lpddr5_mc_apb_metadata_pkg::lpddr5_mc_register_image_t image_i,
  input logic [31:0] boot_epoch_i, config_epoch_i,
  output lpddr5_mc_host_pkg::lpddr5_mc_host_config_t config_o
);
  import lpddr5_mc_apb_metadata_pkg::*;
  logic unused_image;
  always_comb begin
    config_o = '0;
    config_o.qos_map = image_i[INDEX_QOS_MAP];
    config_o.granule64 = image_i[INDEX_MODE][0];
    config_o.inline_ecc = image_i[INDEX_MODE][1];
    config_o.posted = image_i[INDEX_MODE][4];
    config_o.read_limit[0] = image_i[INDEX_PORT_LIMIT_0][7:0];
    config_o.write_limit[0] = image_i[INDEX_PORT_LIMIT_0][15:8];
    config_o.read_limit[1] = image_i[INDEX_PORT_LIMIT_1][7:0];
    config_o.write_limit[1] = image_i[INDEX_PORT_LIMIT_1][15:8];
    config_o.boot_epoch = boot_epoch_i; config_o.config_epoch = config_epoch_i;
    for (int r=0; r<8; r++) begin
      config_o.regions[r].enable = image_i[REGION_ATTR_INDEX[r]][0];
      config_o.regions[r].base = {image_i[REGION_BASE_HI_INDEX[r]][7:0],image_i[REGION_BASE_LO_INDEX[r]]};
      config_o.regions[r].size = {1'b0,image_i[REGION_SIZE_4K_INDEX[r]][27:0],12'b0};
      for (int b=0; b<40; b++) config_o.regions[r].permutation[b] = image_i[REGION_BIT_INDEX[r][b]][5:0];
    end
    // The complete canonical image includes channel-owned timing/diagnostics.
    unused_image = ^image_i;
  end
endmodule
