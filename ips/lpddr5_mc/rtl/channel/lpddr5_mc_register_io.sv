// Exclusive MRR/background-ZQ owner. The caller transfers the pin mux only
// after real host-data/pin drain, page recovery and WCK disable. Return data
// travels through the same canonical PHY bus as normal reads.
module lpddr5_mc_register_io (
  input logic clk_ck_i, rst_ni, destructive_reset_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_profile_t profile_i,
  input logic [31:0] boot_epoch_i, config_epoch_i,
  input logic request_valid_i,
  output logic request_ready_o,
  input logic request_rank_i, request_zq_i,
  input logic [6:0] request_address_i,
  input logic exclusive_i,
  output logic busy_o, result_valid_o,
  input logic result_ready_i,
  output logic result_rank_o, result_zq_o, result_failed_o,
  output logic [6:0] result_address_o,
  output logic [7:0] result_value_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_ca_t ca_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_wck4_t wck_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_read_request4_t read_request_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_return4_t return_i,
  output logic fault_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  typedef enum logic [3:0] {IDLE,SYNC,READ,READ_WAIT,STOP,DISABLE,ZQ,ZQ_WAIT,RESULT} state_t;
  state_t state_q;
  lpddr5_mc_profile_t read_profile;
  lpddr5_mc_granule_t launch;
  lpddr5_mc_channel_identity_t identity;
  lpddr5_mc_channel_completion_t completion;
  lpddr5_mc_write_bus4_t unused_write;
  logic data_ready,data_valid,data_drained,data_fault;
  logic wck_disabled,wck_reusable,wck_fault,wck_rank,wck_write,stop_ready;
  logic [63:0] last_pin,tag_q;
  logic [31:0] elapsed_q,mrr_age_q;
  logic mrr_recovered;
  logic unused_debug;
  assign unused_debug=^{unused_write,last_pin,wck_reusable,wck_rank,wck_write,profile_i,completion};
  assign busy_o=state_q!=IDLE || fault_o;
  assign request_ready_o=state_q==IDLE && exclusive_i && !fault_o && tag_q!='1;
  assign result_valid_o=state_q==RESULT;
  // JESD209-5C section 7.6.1 and MR43-45 read-clear notes measure
  // recovery from MRR, concurrently with its return and WCK shutdown.
  // Keep the stronger read-clear sum for every address. Data drain and
  // full WCK disable remain separate prerequisites for owner handback.
  assign mrr_recovered=mrr_age_q+1>=32'(profile_i.dram_ck_tmrr)+32'(profile_i.dram_ck_tmrw);
  always_comb begin
    // JEDEC MRR disables DBI and uses the opcode bus rather than a protected
    // normal-read codeword. Its undefined later UI are never interpreted.
    read_profile=profile_i; read_profile.read_link=0; read_profile.read_dbi=0;
    launch='0; launch.tag=tag_q; launch.rank_index=result_rank_o;
    identity='0; identity.tag=tag_q; identity.boot_epoch=boot_epoch_i; identity.config_epoch=config_epoch_i;
    ca_o='0;
    if(state_q==SYNC) begin ca_o.cs=2'b1<<result_rank_o; ca_o.address=ca_cas(3'd2); end
    if(state_q==READ) begin ca_o.cs=2'b1<<result_rank_o; ca_o.address=ca_pack(7'd24,result_address_o); end
    if(state_q==STOP && stop_ready && mrr_recovered) begin ca_o.cs=2'b1<<result_rank_o; ca_o.address=ca_cas(3'd7); end
    // Table 303, OP[7:0]=0x86: ZQ CAL Latch, background mode only.
    if(state_q==ZQ) begin ca_o.cs=2'b1<<result_rank_o; ca_o.address=ca_pack(7'd112,7'd6); end
  end
  lpddr5_mc_wck wck(.clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.abort_i(destructive_reset_i),.profile_i(read_profile),
    .sync_i(state_q==SYNC),.sync_write_i(1'b0),.sync_rank_i(result_rank_o),
    .access_i(state_q==READ),.access_write_i(1'b0),.access_bl32_i(1'b0),.stop_i(state_q==STOP && stop_ready && mrr_recovered),
    .stop_ready_o(stop_ready),.disabled_o(wck_disabled),.reusable_o(wck_reusable),.fault_o(wck_fault),.active_rank_o(wck_rank),.active_write_o(wck_write),.wck_o(wck_o));
  lpddr5_mc_data #(.DEPTH(2)) data_path(.clk_ck_i(clk_ck_i),.rst_ni(rst_ni),.abort_i(destructive_reset_i),.profile_i(read_profile),
    .launch_valid_i(state_q==READ),.launch_ready_o(data_ready),.launch_i(launch),.identity_i(identity),
    .done_valid_o(data_valid),.done_ready_i(state_q==READ_WAIT),.done_o(completion),.write_o(unused_write),
    .read_request_o(read_request_o),.return_i(return_i),.drained_o(data_drained),.fault_o(data_fault),.last_pin_half_wck_o(last_pin));
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if(!rst_ni) begin
      state_q<=IDLE; elapsed_q<=0; mrr_age_q<=0; tag_q<=0; fault_o<=0;
      result_rank_o<=0; result_zq_o<=0; result_failed_o<=0; result_address_o<=0; result_value_o<=0;
    end else if(destructive_reset_i) begin
      state_q<=IDLE; elapsed_q<=0; mrr_age_q<=0; tag_q<=0; fault_o<=0; result_failed_o<=0;
    end else begin
      elapsed_q<=elapsed_q+1;
      if(mrr_age_q!='1) mrr_age_q<=mrr_age_q+1;
      case(state_q)
        IDLE: if(request_valid_i && request_ready_o) begin
          result_rank_o<=request_rank_i; result_zq_o<=request_zq_i; result_address_o<=request_address_i;
          result_failed_o<=0; result_value_o<=0; elapsed_q<=0; tag_q<=tag_q+1;
          state_q<=request_zq_i ? ZQ : SYNC;
        end
        SYNC: state_q<=READ;
        READ: begin if(!data_ready) fault_o<=1; elapsed_q<=0; mrr_age_q<=0; state_q<=READ_WAIT; end
        READ_WAIT: if(data_valid) begin
          result_value_o<=completion.data[7:0]; result_failed_o<=completion.failed;
          for(int beat=1;beat<8;beat++) if(completion.data[beat*16+:8]!=completion.data[7:0]) result_failed_o<=1;
          state_q<=STOP; elapsed_q<=0;
        end
        STOP: if(wck_disabled || (stop_ready && mrr_recovered)) state_q<=DISABLE;
        DISABLE: if(wck_disabled && data_drained && mrr_recovered) state_q<=RESULT;
        ZQ: begin state_q<=ZQ_WAIT; elapsed_q<=0; end
        // elapsed starts at the DFI MPC edge. Unlike the next controller CA
        // (which has the same path delay), PHY ownership can act immediately
        // at the pins. Retain the register owner through physical latch
        // arrival plus tZQLAT before publishing completion.
        ZQ_WAIT: if(elapsed_q+1>=32'(profile_i.management_ck_tctrl_delay)+32'(profile_i.dram_ck_tzqlat)) state_q<=RESULT;
        RESULT: if(result_ready_i) state_q<=IDLE;
        default: fault_o<=1;
      endcase
      if(state_q!=IDLE && (!exclusive_i || elapsed_q>32'(profile_i.management_ck_boot_timeout))) fault_o<=1;
      if(data_fault || wck_fault) fault_o<=1;
    end
  end
endmodule
