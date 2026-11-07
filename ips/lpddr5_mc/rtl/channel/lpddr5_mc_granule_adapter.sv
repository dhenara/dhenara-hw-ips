// Logical-to-physical tracking. Masked BL32 becomes two distinct MWR16s;
// internal tags are never reused inside a boot epoch. Final ready/valid moves
// every result obligation into the caller's reserved response storage.
module lpddr5_mc_granule_adapter #(
  parameter int DEPTH=64,
  parameter int CHANNEL_INDEX=0
) (
  input logic clk_ck_i,rst_ni,destructive_reset_i,admission_i,
  input logic [31:0] boot_epoch_i,config_epoch_i,
  input logic [1:0] rank_count_i,
  input logic request_valid_i,
  output logic request_ready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_granule_t request_i,
  output logic physical_valid_o,
  input logic physical_ready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_granule_t physical_o,
  input logic result_valid_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_completion_t result_i,
  output logic result_ready_o,
  output logic completion_valid_o,
  input logic completion_ready_i,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_completion_t completion_o,
  output logic drained_o,fault_o,
  output logic [63:0] stale_results_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  typedef struct packed {
    logic valid,split,failed,corrected,uncorrectable;
    logic [8:0] syndrome;
    logic [1:0] sent,allocated,physical,settled;
    logic [1:0][63:0] tags;
    lpddr5_mc_granule_t granule;
    lpddr5_mc_channel_identity_t identity;
    logic [511:0] data;
  } slot_t;
  slot_t [DEPTH-1:0] slots_q;
  logic [63:0] sequence_q;
  int free_slot,send_slot,done_slot,match_slot;
  logic free_found,duplicate;
  logic send_half,match_half;
  int output_slot_q,command_slot_q;
  logic command_half_q;
  logic [1:0] needed;
  logic unused_payload;
  assign unused_payload=^{request_i,result_i};
  always_comb begin
    free_slot=-1;send_slot=-1;done_slot=-1;match_slot=-1;
    send_half=0;match_half=0;duplicate=0;free_found=0;drained_o=1;needed=0;
    for(int s=0;s<DEPTH;s++) begin
      if(!slots_q[s].valid && !free_found) begin free_slot=s;free_found=1; end
      if(slots_q[s].valid) begin
        drained_o=0;
        if(slots_q[s].identity.tag==request_i.tag && slots_q[s].identity.generation==request_i.generation
            && slots_q[s].identity.boot_epoch==boot_epoch_i && slots_q[s].identity.config_epoch==config_epoch_i) duplicate=1;
        needed=slots_q[s].split ? 2'b11 : 2'b01;
        if(send_slot<0) for(int h=0;h<2;h++)
          if(needed[h] && !slots_q[s].allocated[h] && send_slot<0) begin send_slot=s;send_half=1'(h); end
        if((slots_q[s].settled & needed)==needed && done_slot<0) done_slot=s;
        for(int h=0;h<2;h++) if(slots_q[s].sent[h] && slots_q[s].tags[h]==result_i.identity.tag
            && result_i.identity.generation==0 && slots_q[s].identity.boot_epoch==result_i.identity.boot_epoch
            && slots_q[s].identity.config_epoch==result_i.identity.config_epoch) begin match_slot=s;match_half=1'(h); end
      end
    end
    request_ready_o=admission_i && free_found && !duplicate && !fault_o && sequence_q!='1;
    result_ready_o=1;
  end
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if(!rst_ni) begin
      for(int s=0;s<DEPTH;s++) slots_q[s]<='0;
      sequence_q<=1;physical_valid_o<=0;physical_o<='0;
      completion_valid_o<=0;completion_o<='0;fault_o<=0;stale_results_o<=0;
      output_slot_q<=0;command_slot_q<=0;command_half_q<=0;
    end else if(destructive_reset_i) begin
      for(int s=0;s<DEPTH;s++) slots_q[s]<='0;
      sequence_q<=1;physical_valid_o<=0;completion_valid_o<=0;fault_o<=0;
    end else begin
      if(request_valid_i && duplicate) fault_o<=1;
      if(request_valid_i && request_ready_o) begin
        slots_q[free_slot]<='0;slots_q[free_slot].valid<=1;slots_q[free_slot].granule<=request_i;
        slots_q[free_slot].split<=request_i.is_write && request_i.is_64_bytes && request_i.byte_enable!='1;
        slots_q[free_slot].identity.tag<=request_i.tag;slots_q[free_slot].identity.generation<=request_i.generation;
        slots_q[free_slot].identity.boot_epoch<=boot_epoch_i;slots_q[free_slot].identity.config_epoch<=config_epoch_i;
        if(request_i.channel_index!=1'(CHANNEL_INDEX) || int'(request_i.rank_index)>=int'(rank_count_i)
            || request_i.column[11:6]!=0 || request_i.row[17:15]!=0 || (request_i.is_64_bytes && request_i.column[0])) begin
          slots_q[free_slot].allocated<=3;slots_q[free_slot].settled<=3;slots_q[free_slot].failed<=1;
        end
      end
      if(physical_valid_o && physical_ready_i) begin
        physical_valid_o<=0;slots_q[command_slot_q].sent[command_half_q]<=1;
      end
      if(!physical_valid_o && send_slot>=0 && sequence_q!='1 && !fault_o) begin
        physical_valid_o<=1;physical_o<=slots_q[send_slot].granule;
        physical_o.tag<=sequence_q;physical_o.generation<=0;
        slots_q[send_slot].allocated[send_half]<=1;slots_q[send_slot].tags[send_half]<=sequence_q;
        command_slot_q<=send_slot;command_half_q<=send_half;sequence_q<=sequence_q+1;
        if(slots_q[send_slot].split) begin
          physical_o.is_64_bytes<=0;
          physical_o.column<=slots_q[send_slot].granule.column+12'(send_half);
          physical_o.address<=slots_q[send_slot].granule.address+(send_half ? 40'd32 : 40'd0);
          physical_o.data<=send_half ? {256'd0,slots_q[send_slot].granule.data[511:256]} : {256'd0,slots_q[send_slot].granule.data[255:0]};
          physical_o.byte_enable<=send_half ? {32'd0,slots_q[send_slot].granule.byte_enable[63:32]} : {32'd0,slots_q[send_slot].granule.byte_enable[31:0]};
        end
      end
      if(result_valid_i) begin
        if(match_slot<0) begin if(stale_results_o!='1) stale_results_o<=stale_results_o+1; end
        else if(slots_q[match_slot].settled[match_half] || result_i.is_write!=slots_q[match_slot].granule.is_write) fault_o<=1;
        else begin
          slots_q[match_slot].physical[match_half]<=slots_q[match_slot].physical[match_half] || result_i.physical_done;
          if(result_i.integrity_done) begin
            slots_q[match_slot].settled[match_half]<=1;
            slots_q[match_slot].corrected<=slots_q[match_slot].corrected || result_i.corrected;
            slots_q[match_slot].uncorrectable<=slots_q[match_slot].uncorrectable || result_i.uncorrectable;
            if((result_i.corrected || result_i.uncorrectable) && !slots_q[match_slot].corrected && !slots_q[match_slot].uncorrectable) slots_q[match_slot].syndrome<=result_i.syndrome;
            slots_q[match_slot].failed<=slots_q[match_slot].failed || result_i.failed || !result_i.physical_done;
            if(!slots_q[match_slot].granule.is_write) slots_q[match_slot].data<=result_i.data;
          end
        end
      end
      if(completion_valid_o && completion_ready_i) begin completion_valid_o<=0;slots_q[output_slot_q].valid<=0; end
      if(!completion_valid_o && done_slot>=0) begin
        completion_valid_o<=1;output_slot_q<=done_slot;completion_o<='0;
        completion_o.identity<=slots_q[done_slot].identity;completion_o.is_write<=slots_q[done_slot].granule.is_write;
        completion_o.physical_done<=slots_q[done_slot].split ? &slots_q[done_slot].physical : slots_q[done_slot].physical[0];
        completion_o.integrity_done<=1;completion_o.failed<=slots_q[done_slot].failed;
        completion_o.corrected<=slots_q[done_slot].corrected;completion_o.uncorrectable<=slots_q[done_slot].uncorrectable;
        completion_o.syndrome<=slots_q[done_slot].syndrome;
        completion_o.response<=slots_q[done_slot].failed ? 2'b10 : 2'b00;
        completion_o.data<=slots_q[done_slot].data;
      end
    end
  end
endmodule
