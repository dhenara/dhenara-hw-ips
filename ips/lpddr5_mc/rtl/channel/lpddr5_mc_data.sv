// Pin-order DFI transfer tracker. Each slot owns all words and both byte slices
// until the final physical edge and complete returned data have been observed.
module lpddr5_mc_data #(
  parameter int DEPTH = 64
) (
  input logic clk_ck_i, rst_ni,
  input logic abort_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_profile_t profile_i,
  input logic launch_valid_i,
  output logic launch_ready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_granule_t launch_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_identity_t identity_i,
  output logic done_valid_o,
  input logic done_ready_i,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_completion_t done_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_write_bus4_t write_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_read_request4_t read_request_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_return4_t return_i,
  output logic drained_o, fault_o,
  output logic [63:0] last_pin_half_wck_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  import lpddr5_mc_ecc_pkg::*;
  typedef struct packed {
    logic live, write_access, bl32, failed;
    logic rank_index;
    logic [63:0] command_ck, pin_end, safe_end;
    lpddr5_mc_channel_identity_t identity;
    logic [511:0] wire_data;
    logic [63:0] dmi, parity;
    logic [1:0][4:0] received;
    logic [511:0] read_data;
    logic [63:0] read_parity;
  } slot_t;
  slot_t [DEPTH-1:0] slots_q, slots_d;
  logic [63:0] ck_q;
  logic [1:0][1:0] pointer_q, pointer_d;
  logic fault_d;
  logic done_hold_q;
  int done_index_q;
  logic free_found, done_found;
  int free_slot, done_slot;
  int ratio, words, offset_word, lane, oldest, next_word, count_valid;
  logic [63:0] data_at, sample_at, minimum_command, enable_at;
  logic [7:0] value;
  logic invert;
  logic [127:0] group_data;
  logic [15:0] group_dmi;
  logic [8:0] group_checks;
  logic [5:0] dmi_checks;

  logic unused_payload;
  assign unused_payload = ^{profile_i,launch_i,done_decoded.syndrome};

  assign ratio=int'(profile_i.ratio);
  always_comb begin
    slots_d=slots_q; pointer_d=pointer_q; fault_d=fault_o;

    write_o='0; read_request_o='0;
    words=0; offset_word=0; lane=0; oldest=0; next_word=0; count_valid=0;
    data_at=0; sample_at=ck_q*64'(ratio); minimum_command='1; enable_at=0;
    value=0; invert=0; group_data=0; group_dmi=0;
    group_checks=0; dmi_checks=0;
    for (int s=0; s<DEPTH; s++) begin
      if (slots_q[s].live) begin
        words=slots_q[s].bl32 ? 16 : 8;
        for (int w=0; w<16; w++) begin
          offset_word=w + ((ratio==4 && slots_q[s].bl32 && w>=8) ? 8 : 0);
          enable_at=slots_q[s].command_ck*64'(ratio)
            +(slots_q[s].write_access ? 64'(profile_i.dfi_wck_tphy_wrlat) : 64'(profile_i.dfi_wck_trddata_en))
            +64'(offset_word);
          if (w<words) begin
            for (int l=0; l<4; l++) begin
              data_at=sample_at+64'(l);
              if (l<ratio && data_at==enable_at) begin
                if (slots_q[s].write_access) begin
                  if (write_o.enable[l]!=0) fault_d=1;
                  write_o.enable[l]='1;
                end else begin
                  if (read_request_o.enable[l]!=0) fault_d=1;
                  read_request_o.enable[l]='1;
                end
              end
              if (slots_q[s].write_access && l<ratio
                  && data_at==enable_at+64'(profile_i.dfi_wck_tphy_wrdata)) begin
                write_o.data[l]=slots_q[s].wire_data[w*32+:32];
                write_o.mask[l]=slots_q[s].dmi[w*4+:4];
                write_o.link_parity[l]=slots_q[s].parity[w*4+:4];
              end
            end
          end
        end
        for (int l=0; l<4; l++) begin
          data_at=sample_at+64'(l);
          enable_at=slots_q[s].command_ck*64'(ratio)
            +(slots_q[s].write_access ? 64'(profile_i.dfi_wck_tphy_wrcslat) : 64'(profile_i.dfi_wck_tphy_rdcslat));
          if (l<ratio && data_at>=enable_at && data_at<enable_at+(64'(words)+(ratio==4 && words==16 ? 64'd8 : 64'd0))) begin
            if (slots_q[s].write_access) begin
              if (write_o.cs[l]!=0 && write_o.cs[l]!=(2'b1<<slots_q[s].rank_index)) fault_d=1;
              write_o.cs[l]=2'b1<<slots_q[s].rank_index;
            end else begin
              for (int b=0; b<2; b++) begin
                if (read_request_o.cs[l][b]!=0 && read_request_o.cs[l][b]!=(2'b1<<slots_q[s].rank_index)) fault_d=1;
                read_request_o.cs[l][b]=2'b1<<slots_q[s].rank_index;
              end
            end
          end
        end
      end
    end

    // A byte slice can pause independently. Within each snapshot its asserted
    // word lanes must be a contiguous prefix of its rotating lane order.
    for (int b=0; b<2; b++) begin
      count_valid=0;
      for (int ordinal=0; ordinal<4; ordinal++) begin
        lane=(int'(pointer_q[b])+ordinal)%((ratio==2) ? 2 : 4);
        if (ordinal<ratio && return_i.valid[lane][b]) begin
          if (count_valid!=ordinal) fault_d=1;
          count_valid++;
          oldest=-1; minimum_command='1;
          for (int s=0; s<DEPTH; s++) begin
            if (slots_d[s].live && !slots_d[s].write_access
                && int'(slots_d[s].received[b])<(slots_d[s].bl32 ? 16 : 8)
                ) begin
              next_word=int'(slots_d[s].received[b]);
              offset_word=next_word+((ratio==4 && slots_d[s].bl32 && next_word>=8) ? 8 : 0);
              enable_at=slots_d[s].command_ck*64'(ratio)+64'(profile_i.dfi_wck_trddata_en)+64'(offset_word);
              if (enable_at<minimum_command) begin oldest=s; minimum_command=enable_at; end
            end
          end
          if (oldest<0) fault_d=1;
          else begin
            next_word=int'(slots_d[oldest].received[b]);
            offset_word=next_word+((ratio==4 && slots_d[oldest].bl32 && next_word>=8) ? 8 : 0);
            enable_at=minimum_command;
            if (sample_at<enable_at || sample_at>enable_at+64'(profile_i.dfi_wck_tphy_rdlat)) fault_d=1;
            for (int beat=0; beat<2; beat++) begin
              slots_d[oldest].read_data[next_word*32+beat*16+b*8+:8]=return_i.data[lane][beat*16+b*8+:8];
              slots_d[oldest].read_parity[next_word*4+beat*2+b]=return_i.link_parity[lane][beat*2+b];
              if (return_i.dnv[lane][beat*2+b]) slots_d[oldest].failed=1;
              if (profile_i.read_dbi && return_i.link_parity[lane][beat*2+b])
                slots_d[oldest].read_data[next_word*32+beat*16+b*8+:8]^=8'hff;
            end
            slots_d[oldest].received[b]++;
          end
        end
      end
      if (ratio==2 && (return_i.valid[2][b] || return_i.valid[3][b])) fault_d=1;
      pointer_d[b]=2'((int'(pointer_q[b])+count_valid)%((ratio==2) ? 2 : 4));
    end

    for (int s=0; s<DEPTH; s++) begin
      words=slots_q[s].bl32 ? 16 : 8;
      if (slots_q[s].live && !slots_q[s].write_access) begin
        for (int b=0; b<2; b++) begin
          next_word=int'(slots_d[s].received[b]);
          offset_word=next_word+((ratio==4 && slots_q[s].bl32 && next_word>=8) ? 8 : 0);
          enable_at=slots_q[s].command_ck*64'(ratio)+64'(profile_i.dfi_wck_trddata_en)+64'(offset_word);
          if (next_word<words && sample_at>enable_at+64'(profile_i.dfi_wck_tphy_rdlat)) fault_d=1;
        end
      end
    end
    if (done_valid_o && done_ready_i) slots_d[done_slot].live=0;
    if (launch_valid_i && launch_ready_o) begin
      slots_d[free_slot]='0;
      slots_d[free_slot].live=1;
      slots_d[free_slot].write_access=launch_i.is_write;
      slots_d[free_slot].bl32=launch_i.is_64_bytes;
      slots_d[free_slot].rank_index=launch_i.rank_index;
      slots_d[free_slot].identity=identity_i;
      slots_d[free_slot].command_ck=ck_q;
      // Half-WCK physical time includes command/serialization paths and DQ
      // offsets. A separate output-path flush can extend write ownership.
      slots_d[free_slot].pin_end=ck_q*64'(2*ratio)+64'(profile_i.phy_half_wck_command)
        +(launch_i.is_write ? 64'(profile_i.wl_ck) : 64'(profile_i.rl_ck))*64'(2*ratio)
        +(launch_i.is_write ? 64'(profile_i.phy_half_wck_dq_input) : 64'(profile_i.phy_half_wck_dq_output))
        +64'(launch_i.is_64_bytes ? (ratio==4 ? 48 : 32) : 16);
      slots_d[free_slot].safe_end=slots_d[free_slot].pin_end;
      if (launch_i.is_write) begin
        enable_at=ck_q*64'(2*ratio)+2*(64'(profile_i.dfi_wck_tphy_wrlat)
          +64'(profile_i.dfi_wck_tphy_wrdata)+64'(profile_i.dfi_wck_twrdata_delay)
          +(launch_i.is_64_bytes ? (ratio==4 ? 64'd24 : 64'd16) : 64'd8));
        if (enable_at>slots_d[free_slot].safe_end) slots_d[free_slot].safe_end=enable_at;
      end
      for (int beat=0; beat<32; beat++) begin
        for (int b=0; b<2; b++) begin
          value=launch_i.data[beat*16+b*8+:8]; invert=0;
          if (!launch_i.byte_enable[beat*2+b]) begin
            value=profile_i.write_dbi ? 8'hfc : 8'b0;
            invert=!profile_i.write_dbi;
          end else if (profile_i.write_dbi && $countones(value)>4) begin value=~value; invert=1; end
          slots_d[free_slot].wire_data[beat*16+b*8+:8]=value;
          slots_d[free_slot].dmi[beat*2+b]=invert;
        end
      end
      if (profile_i.write_link) begin
        for (int g=0; g<2; g++) begin
          for (int b=0; b<2; b++) begin
            group_data=0; group_dmi=0;
            for (int beat=0; beat<16; beat++) begin
              group_data[beat*8+:8]=slots_d[free_slot].wire_data[(g*16+beat)*16+b*8+:8];
              group_dmi[beat]=slots_d[free_slot].dmi[(g*16+beat)*2+b];
            end
            group_checks=link_encode(group_data); dmi_checks=dmi_encode(group_dmi);
            for (int beat=0; beat<16; beat++)
              slots_d[free_slot].parity[(g*16+beat)*2+b]=beat==0 ? 1'b0 : beat<7 ? dmi_checks[beat-1] : group_checks[beat-7];
          end
        end
      end
    end
  end

  int done_words;
  logic [127:0] done_group_data;
  logic [8:0] done_group_checks;
  link_result_t done_decoded;
  always_comb begin
    free_found=0; free_slot=0; drained_o=1;
    for(int s=0;s<DEPTH;s++) begin
      if(!slots_q[s].live && !free_found) begin free_found=1;free_slot=s; end
      if(slots_q[s].live) drained_o=0;
    end
    launch_ready_o=free_found && !fault_o;
  end
  always_comb begin
    done_found=0;done_slot=0;done_words=0;done_o='0;
    done_group_data=0;done_group_checks=0;done_decoded='0;
    for(int s=0;s<DEPTH;s++) begin
      done_words=slots_q[s].bl32 ? 16 : 8;
      if (slots_q[s].live && ck_q*64'(2*ratio)>=slots_q[s].safe_end
          && (slots_q[s].write_access || (int'(slots_q[s].received[0])==done_words && int'(slots_q[s].received[1])==done_words))
          && !done_found) begin done_found=1; done_slot=s; end
    end
    if (done_hold_q) begin done_found=1; done_slot=done_index_q; end
    done_valid_o=done_found;
    if (done_found) begin
      done_o.identity=slots_q[done_slot].identity;
      done_o.physical_done=1; done_o.integrity_done=1;
      done_o.is_write=slots_q[done_slot].write_access;
      done_o.data=slots_q[done_slot].read_data;
      done_o.failed=slots_q[done_slot].failed;
      if (!slots_q[done_slot].write_access && profile_i.read_link) begin
        for (int g=0; g<2; g++) begin
          for (int b=0; b<2; b++) begin
            done_group_data=0; done_group_checks=0;
            for (int beat=0; beat<16; beat++) begin
              done_group_data[beat*8+:8]=slots_q[done_slot].read_data[(g*16+beat)*16+b*8+:8];
              if (beat>=7) done_group_checks[beat-7]=slots_q[done_slot].read_parity[(g*16+beat)*2+b];
              else if (slots_q[done_slot].read_parity[(g*16+beat)*2+b] && (g==0 || slots_q[done_slot].bl32)) done_o.failed=1;
            end
            done_decoded=link_decode(done_group_data,done_group_checks);
            if (g==0 || slots_q[done_slot].bl32) begin
              if ((done_decoded.corrected || done_decoded.uncorrectable) && !done_o.corrected && !done_o.uncorrectable) done_o.syndrome=done_decoded.syndrome;
              done_o.corrected|=done_decoded.corrected;
              done_o.uncorrectable|=done_decoded.uncorrectable;
              if (done_decoded.uncorrectable) done_o.failed=1;
              for (int beat=0; beat<16; beat++) done_o.data[(g*16+beat)*16+b*8+:8]=done_decoded.data[beat*8+:8];
            end
          end
        end
      end
      done_o.uncorrectable|=done_o.failed;
      done_o.response=done_o.failed ? 2'b10 : 2'b00;
    end
  end

  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if (!rst_ni) begin for (int s=0; s<DEPTH; s++) slots_q[s]<='0; ck_q<=0; pointer_q<=0; fault_o<=0; last_pin_half_wck_o<=0; done_hold_q<=0; done_index_q<=0; end
    else if (abort_i) begin for (int s=0; s<DEPTH; s++) slots_q[s]<='0; pointer_q<=0; fault_o<=0; last_pin_half_wck_o<=0; ck_q<=0; done_hold_q<=0; done_index_q<=0; end
    else begin
      if (done_valid_o) begin done_hold_q<=!done_ready_i; done_index_q<=done_slot; end
      ck_q<=ck_q+1; slots_q<=slots_d; pointer_q<=pointer_d; fault_o<=fault_d;
      if (launch_valid_i && launch_ready_o) last_pin_half_wck_o<=slots_d[free_slot].pin_end;
    end
  end
endmodule
