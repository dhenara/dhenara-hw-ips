// Serialized protected-granule owner. One check line is reserved through its
// physical commit; no second operation can observe a half data/check update.
// Corrected reads scrub under the same lock before admitting a newer version.
module lpddr5_mc_inline #(
  parameter int POISON_DEPTH=64,
  parameter int CHANNEL_INDEX=0
) (
  input logic clk_ck_i, rst_ni, destructive_reset_i,
  input logic enable_i, admission_i,
  input logic [31:0] boot_epoch_i, config_epoch_i,
  input logic [1:0] rank_count_i,
  input logic request_valid_i,
  output logic request_ready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_granule_t request_i,
  output logic physical_valid_o,
  input logic physical_ready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_granule_t physical_o,
  input logic result_valid_i,
  output logic result_ready_o,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_completion_t result_i,
  output logic completion_valid_o,
  input logic completion_ready_i,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_completion_t completion_o,
  output logic drained_o, fault_o, corrected_o, uncorrectable_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  import lpddr5_mc_ecc_pkg::*;
  localparam logic [30:0] DATA_BYTES=31'((1073741824/576)*512);
  typedef enum logic [3:0] {IDLE, CHECK_READ, CHECK_WAIT, DATA_READ, DATA_WAIT,
    DECODE, DATA_WRITE, DATA_WRITE_WAIT, CHECK_WRITE, CHECK_WRITE_WAIT, COMPLETE} state_t;
  state_t state_q;
  lpddr5_mc_granule_t request_q;
  lpddr5_mc_channel_identity_t identity_q;
  logic [63:0] sequence_q;
  logic [511:0] check_q, data_q, merged_data;
  logic [511:0] decoded_data;
  logic [63:0] decoded_checks;
  logic [30:0] data_offset_q, check_offset_q, offset_value;
  logic [5:0] check_index_q;
  logic failed_q, corrected_q, uncorrectable_q, data_committed_q, physical_seen_q;
  logic [8:0] syndrome_q,decode_syndrome;
  logic [POISON_DEPTH-1:0] poison_valid_q;
  logic [27:0] poison_word_q[POISON_DEPTH];
  logic [POISON_DEPTH-1:0] poison_rank_q;
  logic poison_hit, poison_any, decode_failed, decode_corrected;
  int free_poison, affected_words;
  int poison_index[8];
  logic [POISON_DEPTH-1:0] allocated;
  inline_result_t decoded;
  logic unused_result;
  assign unused_result=^{result_i,decoded.syndrome};

  function automatic logic [30:0] local_offset(input lpddr5_mc_granule_t g);
    logic unused_granule;
    unused_granule=^g;
    return {1'b0,g.row[14:0],g.bank_group,g.bank,g.column[5:0],5'b0};
  endfunction
  always_comb begin
    request_ready_o=state_q==IDLE && admission_i && enable_i && !fault_o;
    drained_o=state_q==IDLE;
    physical_o=request_q; physical_o.tag=sequence_q; physical_o.generation=0;
    physical_o.is_64_bytes=1; physical_o.is_write=state_q inside {DATA_WRITE,CHECK_WRITE};
    physical_o.byte_enable='1;
    physical_o.data=state_q==CHECK_WRITE ? check_q : data_q;
    offset_value=state_q inside {CHECK_READ,CHECK_WAIT,CHECK_WRITE,CHECK_WRITE_WAIT} ? check_offset_q : data_offset_q;
    physical_o.row={3'b0,offset_value[29:15]}; physical_o.bank_group=offset_value[14:13];
    physical_o.bank=offset_value[12:11]; physical_o.column={6'b0,offset_value[10:5]};
    // All data transfers use one 64B row-contained aligned physical granule.
    physical_valid_o=state_q inside {CHECK_READ,DATA_READ,DATA_WRITE,CHECK_WRITE};
    result_ready_o=state_q inside {CHECK_WAIT,DATA_WAIT,DATA_WRITE_WAIT,CHECK_WRITE_WAIT};
    completion_valid_o=state_q==COMPLETE;
    completion_o='0; completion_o.identity=identity_q; completion_o.is_write=request_q.is_write;
    completion_o.physical_done=physical_seen_q; completion_o.integrity_done=1; completion_o.failed=failed_q;
    completion_o.corrected=corrected_q;completion_o.uncorrectable=uncorrectable_q;completion_o.syndrome=syndrome_q;
    completion_o.response=failed_q ? 2'b10 : 2'b00;
    completion_o.data=failed_q ? 512'd0 : (request_q.is_64_bytes ? data_q : request_q.column[0] ? {256'd0,data_q[511:256]} : {256'd0,data_q[255:0]});
    decode_failed=0; decode_corrected=0;decode_syndrome=0; decoded_data=data_q; decoded_checks=0; decoded='0;
    merged_data=data_q; poison_any=0; free_poison=-1; poison_hit=0; affected_words=0;
    allocated=poison_valid_q;
    for (int w=0;w<8;w++) begin
      poison_index[w]=-1;
      if (request_q.is_64_bytes || w/4==int'(request_q.column[0])) begin
        for(int p=0;p<POISON_DEPTH;p++)
          if (!allocated[p] && poison_index[w]<0) begin poison_index[w]=p; allocated[p]=1; end
      end
    end
    for (int w=0;w<8;w++) begin
      // A 32B logical operation selects one half of the reserved 64B line.
      if (request_q.is_64_bytes || (w/4)==int'(request_q.column[0])) begin
        affected_words++;
        poison_hit=0;
        for (int p=0;p<POISON_DEPTH;p++)
          if (poison_valid_q[p] && poison_rank_q[p]==request_q.rank_index && poison_word_q[p]==28'(data_offset_q[30:3])+28'(w)) poison_hit=1;
        poison_any|=poison_hit;
        if (!request_q.is_write || request_q.byte_enable[(request_q.is_64_bytes ? w : w%4)*8+:8]!=8'hff) begin
          decoded=inline_decode(data_q[w*64+:64],check_q[(int'(check_index_q)+w)*8+:8]);
          if((decoded.uncorrectable || poison_hit) && !decode_failed) decode_syndrome=9'(decoded.syndrome);
          else if(decoded.corrected && !decode_corrected && !decode_failed) decode_syndrome=9'(decoded.syndrome);
          decode_failed|=decoded.uncorrectable || poison_hit;
          decode_corrected|=decoded.corrected;
          decoded_data[w*64+:64]=decoded.data;
        end
        if (request_q.is_write) begin
          for (int b=0;b<8;b++) begin
            if (request_q.byte_enable[(request_q.is_64_bytes ? w : w%4)*8+b])
              decoded_data[w*64+b*8+:8]=request_q.data[(request_q.is_64_bytes ? w : w%4)*64+b*8+:8];
          end
        end
      end
      decoded_checks[w*8+:8]=inline_encode(decoded_data[w*64+:64]);
    end
    merged_data=decoded_data;
  end
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q<=IDLE; request_q<='0; identity_q<='0; sequence_q<=1; check_q<=0; data_q<=0;
      data_offset_q<=0; check_offset_q<=0; check_index_q<=0; failed_q<=0; corrected_q<=0;uncorrectable_q<=0;syndrome_q<=0;
      data_committed_q<=0; physical_seen_q<=0; poison_valid_q<=0; poison_rank_q<=0;
      fault_o<=0; corrected_o<=0; uncorrectable_o<=0;
      for (int p=0;p<POISON_DEPTH;p++) poison_word_q[p]<=0;
    end else if (destructive_reset_i) begin
      state_q<=IDLE; sequence_q<=1; poison_valid_q<=0; fault_o<=0;
      failed_q<=0; corrected_q<=0;uncorrectable_q<=0;syndrome_q<=0; data_committed_q<=0; physical_seen_q<=0; corrected_o<=0; uncorrectable_o<=0;
    end else begin
      corrected_o<=0; uncorrectable_o<=0;
      case (state_q)
        IDLE: if (request_valid_i && request_ready_o) begin
          request_q<=request_i;
          identity_q.tag<=request_i.tag; identity_q.generation<=request_i.generation;
          identity_q.boot_epoch<=boot_epoch_i; identity_q.config_epoch<=config_epoch_i;
          data_offset_q<=local_offset(request_i) & 31'h7fffffc0;
          check_offset_q<=(DATA_BYTES+(local_offset(request_i)>>3)) & 31'h7fffffc0;
          check_index_q<=6'(((local_offset(request_i) & 31'h7fffffc0)>>3)+DATA_BYTES);
          failed_q<=0; corrected_q<=0;uncorrectable_q<=0;syndrome_q<=0; data_committed_q<=0; physical_seen_q<=0;
          state_q<=CHECK_READ;
          if (request_i.channel_index!=1'(CHANNEL_INDEX) || int'(request_i.rank_index)>=int'(rank_count_i)
              || local_offset(request_i)>=DATA_BYTES || request_i.row[17:15]!=0 || request_i.column[11:6]!=0 || (request_i.is_64_bytes && request_i.column[0])) begin failed_q<=1; state_q<=COMPLETE; end
        end
        CHECK_READ, DATA_READ, DATA_WRITE, CHECK_WRITE: if (physical_valid_o && physical_ready_i) begin
          case (state_q)
            CHECK_READ: state_q<=CHECK_WAIT;
            DATA_READ: state_q<=DATA_WAIT;
            DATA_WRITE: state_q<=DATA_WRITE_WAIT;
            default: state_q<=CHECK_WRITE_WAIT;
          endcase
        end
        CHECK_WAIT, DATA_WAIT, DATA_WRITE_WAIT, CHECK_WRITE_WAIT: if (result_valid_i && result_ready_o) begin
          if ((state_q==DATA_WAIT && !request_q.is_write || state_q==DATA_WRITE_WAIT && request_q.is_write)
              && result_i.identity.tag==sequence_q && result_i.identity.boot_epoch==boot_epoch_i
              && result_i.identity.config_epoch==config_epoch_i && result_i.physical_done) physical_seen_q<=1;
          if (result_i.identity.tag!=sequence_q || result_i.identity.boot_epoch!=boot_epoch_i || result_i.identity.config_epoch!=config_epoch_i
              || result_i.identity.generation!=0 || result_i.is_write!=(state_q inside {DATA_WRITE_WAIT,CHECK_WRITE_WAIT})
              || !result_i.integrity_done || !result_i.physical_done || result_i.failed) begin
            failed_q<=1; state_q<=COMPLETE; uncorrectable_o<=1;uncorrectable_q<=1;
            // After a physical update may have begun, poison all words in the
            // locked logical granule. Exhausted detail capacity faults closed.
            if (state_q inside {DATA_WRITE_WAIT,CHECK_WRITE_WAIT}) begin
              for (int w=0;w<8;w++) if (request_q.is_64_bytes || w/4==int'(request_q.column[0])) begin
                if (poison_index[w]>=0) begin
                  poison_valid_q[poison_index[w]]<=1; poison_rank_q[poison_index[w]]<=request_q.rank_index;
                  poison_word_q[poison_index[w]]<=28'(data_offset_q[30:3])+28'(w);
                end else fault_o<=1;
              end
            end
          end else case (state_q)
            CHECK_WAIT: begin check_q<=result_i.data; state_q<=DATA_READ; end
            DATA_WAIT: begin data_q<=result_i.data; state_q<=DECODE; end
            DATA_WRITE_WAIT: begin data_committed_q<=1; state_q<=CHECK_WRITE; end
            default: begin
              state_q<=COMPLETE;
              // A successful complete protected word rewrite clears poison.
              for (int p=0;p<POISON_DEPTH;p++) for(int w=0;w<8;w++)
                if (poison_valid_q[p] && poison_rank_q[p]==request_q.rank_index
                    && poison_word_q[p]==28'(data_offset_q[30:3])+28'(w)
                    && (request_q.is_64_bytes || w/4==int'(request_q.column[0]))) poison_valid_q[p]<=0;
            end
          endcase
          if(result_i.corrected || result_i.uncorrectable) begin
            corrected_q<=corrected_q || result_i.corrected;uncorrectable_q<=uncorrectable_q || result_i.uncorrectable;
            if(!corrected_q && !uncorrectable_q) syndrome_q<=result_i.syndrome;
          end
          if (sequence_q=='1) begin fault_o<=1; failed_q<=1; state_q<=COMPLETE; end
          else sequence_q<=sequence_q+1;
        end
        DECODE: begin
          data_q<=merged_data; corrected_q<=corrected_q || decode_corrected;
          uncorrectable_q<=uncorrectable_q || decode_failed;
          if(decode_failed || (decode_corrected && !corrected_q)) syndrome_q<=decode_syndrome; corrected_o<=decode_corrected; uncorrectable_o<=decode_failed;
          if (decode_failed) begin failed_q<=1; state_q<=COMPLETE; end
          else if (request_q.is_write || decode_corrected) begin
            for (int w=0;w<8;w++) if (request_q.is_64_bytes || w/4==int'(request_q.column[0]))
              check_q[(int'(check_index_q)+w)*8+:8]<=decoded_checks[w*8+:8];
            state_q<=DATA_WRITE;
          end else state_q<=COMPLETE;
        end
        COMPLETE: if (completion_valid_o && completion_ready_i) state_q<=IDLE;
        default: begin fault_o<=1; state_q<=COMPLETE; failed_q<=1; end
      endcase
    end
  end
  logic unused_internal;
  assign unused_internal=^{poison_any,affected_words,corrected_q,data_committed_q,free_poison,allocated,offset_value[30],offset_value[4:0]};
endmodule
