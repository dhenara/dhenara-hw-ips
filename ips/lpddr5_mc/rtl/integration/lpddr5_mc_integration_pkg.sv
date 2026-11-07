// Explicit projections across independently owned interfaces.
package lpddr5_mc_integration_pkg;
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_apb_metadata_pkg::*;

  function automatic lpddr5_mc_host_pkg::lpddr5_mc_host_completion_t host_completion(
      // ECC diagnostics use the independently retained notification path.
      /* verilator lint_off UNUSEDSIGNAL */
      input lpddr5_mc_channel_pkg::lpddr5_mc_channel_completion_t value);
      /* verilator lint_on UNUSEDSIGNAL */
    lpddr5_mc_host_pkg::lpddr5_mc_host_completion_t result;
    result.identity.tag=value.identity.tag;
    result.identity.generation=value.identity.generation;
    result.identity.boot_epoch=value.identity.boot_epoch;
    result.identity.config_epoch=value.identity.config_epoch;
    result.physical_done=value.physical_done;result.integrity_done=value.integrity_done;
    result.failed=value.failed;result.is_write=value.is_write;
    result.data=value.data;result.response=value.response;
    return result;
  endfunction

  function automatic lpddr5_mc_channel_pkg::lpddr5_mc_channel_response_feedback_t channel_feedback(
      input lpddr5_mc_host_pkg::lpddr5_mc_host_feedback_t value);
    lpddr5_mc_channel_pkg::lpddr5_mc_channel_response_feedback_t result;
    result.identity.tag=value.identity.tag;
    result.identity.generation=value.identity.generation;
    result.identity.boot_epoch=value.identity.boot_epoch;
    result.identity.config_epoch=value.identity.config_epoch;
    result.exposed=value.exposed;result.handshaken=value.handshaken;result.response=value.response;
    return result;
  endfunction

  // The APB channel window identifies the channel; ERROR_INFO has no channel bit.
  function automatic lpddr5_mc_register_image_t error_image(
      /* verilator lint_off UNUSEDSIGNAL */
      input lpddr5_mc_error_t error_value,
      input logic [31:0] boot_epoch,config_epoch);
      /* verilator lint_on UNUSEDSIGNAL */
    lpddr5_mc_register_image_t image;
    for(int r=0;r<REG_COUNT;r++) image[r]=0;
    image[INDEX_BOOT_EPOCH]=boot_epoch;image[INDEX_CONFIG_EPOCH]=config_epoch;
    image[INDEX_ERROR_ADDR_LO]=error_value.address[31:0];
    image[INDEX_ERROR_ADDR_HI]={24'b0,error_value.address[39:32]};
    image[INDEX_ERROR_AXI_ID]={16'b0,error_value.axi_id};
    image[INDEX_ERROR_TAG_LO]=error_value.tag[31:0];image[INDEX_ERROR_TAG_HI]=error_value.tag[63:32];
    image[INDEX_ERROR_INFO]={14'b0,1'b0,error_value.operation,error_value.rank_index,error_value.port_index,
      error_value.memory_trusted,error_value.uncorrectable,error_value.correlated,error_value.syndrome};
    return image;
  endfunction
  function automatic lpddr5_mc_register_image_t channel_status_image(
      /* verilator lint_off UNUSEDSIGNAL */
      input lpddr5_mc_channel_pkg::lpddr5_mc_channel_status_t value);
      /* verilator lint_on UNUSEDSIGNAL */
    lpddr5_mc_register_image_t image;
    for(int r=0;r<REG_COUNT;r++) image[r]=0;
    image[INDEX_BOOT_EPOCH]=value.boot_epoch;image[INDEX_CONFIG_EPOCH]=value.config_epoch;
    image[INDEX_DEVICE_MR8]={24'b0,value.device_mr8};
    image[INDEX_DEVICE_MR27]={24'b0,value.device_mr27};
    image[INDEX_DEVICE_MR57]={24'b0,value.device_mr57};
    image[INDEX_TEMP_MR4]={24'b0,value.temperature_mr4};
    {image[INDEX_PERF_PAYLOAD_RD_1],image[INDEX_PERF_PAYLOAD_RD_0]}=value.performance.payload_read;
    {image[INDEX_PERF_PAYLOAD_WR_1],image[INDEX_PERF_PAYLOAD_WR_0]}=value.performance.payload_write;
    {image[INDEX_PERF_COMMANDS_1],image[INDEX_PERF_COMMANDS_0]}=value.performance.commands;
    {image[INDEX_PERF_REPLAYS_1],image[INDEX_PERF_REPLAYS_0]}=value.performance.replays;
    {image[INDEX_PERF_MAINT_CK_1],image[INDEX_PERF_MAINT_CK_0]}=value.performance.maintenance_ck;
    {image[INDEX_PERF_MAX_LATENCY_1],image[INDEX_PERF_MAX_LATENCY_0]}=value.performance.max_latency_ck;
    {image[INDEX_PERF_FAIR_WAIT_1],image[INDEX_PERF_FAIR_WAIT_0]}=value.performance.max_wait_ck;
    {image[INDEX_PERF_CYCLES_1],image[INDEX_PERF_CYCLES_0]}=value.performance.cycles;
    return image;
  endfunction
  function automatic logic [31:0] image_crc(input lpddr5_mc_register_image_t image);
    logic [31:0] crc;
    crc='1;
    for(int r=0;r<REG_COUNT;r++) for(int b=0;b<32;b++)
      crc=(crc>>1) ^ ((crc[0]^image[r][b]) ? 32'hedb88320 : 32'd0);
    return ~crc;
  endfunction
endpackage
