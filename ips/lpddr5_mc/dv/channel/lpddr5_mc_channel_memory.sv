// Bounded behavioral fixture, driven by decoded CA rather than internal DUT
// launches/completions. Independent system qualification remains downstream.
module lpddr5_mc_channel_memory #(
  parameter bit INITIAL_PATTERN=0,
  parameter int CHANNELS=1,CHANNEL_INDEX=0
) (
  input lpddr5_mc_channel_pkg::lpddr5_mc_profile_t profile_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_ca_t ca_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_write_bus4_t write_i,
  input lpddr5_mc_contract_pkg::lpddr5_mc_read_request4_t read_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_return4_t return_o,
  input logic [7:0] mr43_i,
  input logic [4:0] mr4_i,
  output int mr43_count_o,mr4_count_o,zq_count_o,refresh_count_o,rfm_count_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  typedef struct packed {
    logic write_access,mrr,bl32,rank_index;
    logic [6:0] mr_address;
    logic [63:0] address;
    int at_ck;
    logic [15:0] returned;
  } command_t;
  command_t commands[1024];
  byte unsigned memory_bytes[longint unsigned];
  int command_count,ck,read_pointer,raa[2][8],imt,raa_max,raa_decrement;
  logic [17:0] rows[2][16],act_row;
  logic [3:0] act_bank;
  logic act_pending,act_rank;
  logic [31:0] open_banks;
  logic [1:0] write_epoch_pending;
  int first_write_ck[2],last_mr43_ck[2];
  logic [7:0] expected_write,expected_read;
  logic [3:0][1:0] expected_write_cs;
  logic [3:0][1:0][1:0] expected_read_cs;
  int ratio,words,offset_word,enable_at,word_at,rank_value,bank_value;
  longint unsigned byte_address;
  logic [7:0] raw_value,value;
  logic masked;
  // Faults affect the external returned codeword, after parity generation.
  // Bits0..127 are DQ, bits128..136 are the raw DMI parity positions.
  logic [136:0] read_fault_mask=0;
  int read_fault_group=0,read_fault_slice=0;
  logic [127:0] read_group_data;
  logic [1:0][1:0][8:0] read_checks;
  logic unused_debug;
  assign unused_debug=^{profile_i,write_i.link_parity};
  `include "read_link_encoder.svh"
  function automatic byte unsigned stored_byte(input longint unsigned at);
    longint unsigned logical_address;
    if(memory_bytes.exists(at)!=0) return memory_bytes[at];
    logical_address=CHANNELS==1 ? at : (at & 63) | (64'(CHANNEL_INDEX)<<6) | ((at & ~64'd63)<<1);
    return INITIAL_PATTERN ? 8'(logical_address ^ (logical_address>>8) ^ 64'd90) : 8'd0;
  endfunction
  task automatic reset;
    command_count=0;ck=0;read_pointer=0;act_pending=0;act_rank=0;act_row=0;act_bank=0;open_banks=0;
    rfm_count_o=0;for(int r=0;r<2;r++) for(int p=0;p<8;p++) raa[r][p]=0;
    mr43_count_o=0;mr4_count_o=0;zq_count_o=0;refresh_count_o=0;return_o='0;
    memory_bytes.delete();write_epoch_pending=0;read_fault_mask=0;read_fault_group=0;read_fault_slice=0;
    for(int r=0;r<2;r++) begin first_write_ck[r]=0;last_mr43_ck[r]=-1000;end
    for(int r=0;r<2;r++) for(int b=0;b<16;b++) rows[r][b]=0;
  endtask
  // A word is available only after its second DRAM beat reaches the DFI
  // deserializer. Times here are half-WCK, then rounded up to sampled CK.
  function automatic int read_ready_ck(input command_t command, input int word_index);
    int half_ck,split_gap,physical;
    half_ck=2*int'(profile_i.ratio);
    split_gap=profile_i.ratio==4 && command.bl32 && word_index>=8 ? 16 : 0;
    physical=command.at_ck*half_ck+int'(profile_i.phy_half_wck_command)
      +int'(profile_i.rl_ck)*half_ck+int'(profile_i.phy_half_wck_dq_output)
      +2*word_index+split_gap+1+int'(profile_i.phy_half_wck_read);
    return (physical+half_ck-1)/half_ck;
  endfunction
  // The bench calls this before the sampling edge. Returned words advance
  // the persistent DFI rotation, including MRR and either split-BL32 group.
  task automatic prepare;
    int emitted,return_lane;
    emitted=0;return_lane=0;
    ratio=int'(profile_i.ratio);return_o='0;expected_read=0;expected_write=0;
    expected_read_cs=0;expected_write_cs=0;
    words=0;offset_word=0;enable_at=0;word_at=0;byte_address=0;raw_value=0;value=0;masked=0;
    for(int c=0;c<command_count;c++) begin
      words=commands[c].bl32 ? 16 : 8;
      read_checks=0;read_group_data=0;
      if(profile_i.read_link && !commands[c].write_access && !commands[c].mrr) begin
        for(int group_index=0;group_index<(commands[c].bl32 ? 2 : 1);group_index++) begin
          for(int slice_index=0;slice_index<2;slice_index++) begin
            for(int beat=0;beat<16;beat++)
              read_group_data[beat*8+:8]=stored_byte(commands[c].address+64'(group_index)*32+64'(beat)*2+64'(slice_index));
            read_checks[group_index][slice_index]=fixture_read_checks(read_group_data);
          end
        end
      end
      for(int lane=0;lane<ratio;lane++) begin
        word_at=ck*ratio+lane;
        enable_at=commands[c].at_ck*ratio+(commands[c].write_access ? int'(profile_i.dfi_wck_tphy_wrcslat) : int'(profile_i.dfi_wck_tphy_rdcslat));
        if(word_at>=enable_at && word_at<enable_at+words+(ratio==4 && words==16 ? 8 : 0)) begin
          if(commands[c].write_access) expected_write_cs[lane]|=2'b1<<commands[c].rank_index;
          else for(int b=0;b<2;b++) expected_read_cs[lane][b]|=2'b1<<commands[c].rank_index;
        end
      end
      for(int w=0;w<words;w++) begin
        offset_word=w+(ratio==4 && words==16 && w>=8 ? 8 : 0);
        enable_at=commands[c].at_ck*ratio+(commands[c].write_access ? int'(profile_i.dfi_wck_tphy_wrlat) : int'(profile_i.dfi_wck_trddata_en))+offset_word;
        for(int lane=0;lane<ratio;lane++) begin
          word_at=ck*ratio+lane;
          if(word_at==enable_at) begin
            if(commands[c].write_access) begin
              assert(expected_write[lane*2+:2]==0) else $fatal(1,"fixture write collision");expected_write[lane*2+:2]=3;
            end else begin
              assert(expected_read[lane*2+:2]==0) else $fatal(1,"fixture read collision");expected_read[lane*2+:2]=3;
            end
          end
          if(commands[c].write_access && word_at==enable_at+int'(profile_i.dfi_wck_tphy_wrdata)) begin
            for(int b=0;b<4;b++) begin
              raw_value=write_i.data[lane][b*8+:8];
              masked=profile_i.write_dbi ? !write_i.mask[lane][b] && raw_value==8'hfc : write_i.mask[lane][b];
              value=profile_i.write_dbi && write_i.mask[lane][b] ? ~raw_value : raw_value;
              if(!masked) memory_bytes[commands[c].address+64'(w)*4+64'(b)]=value;
            end
          end
        end
        if(!commands[c].write_access && !commands[c].returned[w] && ck>=read_ready_ck(commands[c],w)) begin
          assert(emitted<ratio) else $fatal(1,"fixture read bandwidth collision");
          return_lane=(read_pointer+emitted)%ratio;
          emitted++;commands[c].returned[w]=1;
          assert(ck*ratio<=enable_at+int'(profile_i.dfi_wck_tphy_rdlat))
            else $fatal(1,"fixture missed read deadline");
          assert(return_o.valid[return_lane]==0) else $fatal(1,"fixture return collision");return_o.valid[return_lane]=3;
          for(int b=0;b<4;b++) begin
            byte_address=commands[c].address+64'(w)*4+64'(b);value=0;
            if(commands[c].mrr) begin
              if(w<4 && b%2==0) value=commands[c].mr_address==43 ? mr43_i : {3'b0,mr4_i};
              else value=8'hda;
            end else value=stored_byte(byte_address);
            if(profile_i.read_link && !commands[c].mrr) begin
              int beat_in_group,group_index,slice_index;
              beat_in_group=(w%8)*2+b/2;group_index=w/8;slice_index=b%2;
              if(beat_in_group>=7) return_o.link_parity[return_lane][b]=read_checks[group_index][slice_index][beat_in_group-7];
              if(group_index==read_fault_group && slice_index==read_fault_slice) begin
                value^=read_fault_mask[beat_in_group*8+:8];
                if(beat_in_group>=7) return_o.link_parity[return_lane][b]^=read_fault_mask[128+beat_in_group-7];
              end
            end
            return_o.data[return_lane][b*8+:8]=value;
          end
        end
      end
    end
    read_pointer=(read_pointer+emitted)%ratio;
    assert(write_i.enable==expected_write && read_i.enable==expected_read) else $fatal(1,"CA/DFI enable mismatch at CK%0d",ck);
    assert(write_i.cs==expected_write_cs && read_i.cs==expected_read_cs) else $fatal(1,"continuous CS mismatch at CK%0d",ck);
  endtask
  task automatic sample;
    // Retire only after the last data and continuous-CS slot. Tracking live
    // commands bounds long simulations without changing storage contents.
    int retained,last_data,last_cs,span;
    retained=0;
    for(int c=0;c<command_count;c++) begin
      span=(commands[c].bl32 ? 16 : 8)+(profile_i.ratio==4 && commands[c].bl32 ? 8 : 0);
      last_data=commands[c].at_ck*int'(profile_i.ratio)+span+
        (commands[c].write_access ? int'(profile_i.dfi_wck_tphy_wrlat)+int'(profile_i.dfi_wck_tphy_wrdata) : 0);
      if(!commands[c].write_access)
        last_data=read_ready_ck(commands[c],commands[c].bl32 ? 15 : 7)*int'(profile_i.ratio);
      last_cs=commands[c].at_ck*int'(profile_i.ratio)+span+
        (commands[c].write_access ? int'(profile_i.dfi_wck_tphy_wrcslat) : int'(profile_i.dfi_wck_tphy_rdcslat));
      if(ck*int'(profile_i.ratio)<=last_data || ck*int'(profile_i.ratio)<=last_cs) begin
        commands[retained]=commands[c];retained++;
      end
    end
    command_count=retained;
    if(ca_i.cs!=0) begin
      rank_value=ca_i.cs==2 ? 1 : 0;bank_value=int'(ca_i.address[10:7]);
      if(act_pending) begin
        assert(ca_i.cs==(2'b1<<act_rank) && ca_i.address[2:0]==3) else $fatal(1,"broken ACT pair");
        act_row[10:0]={ca_i.address[6:3],ca_i.address[13:7]};rows[act_rank][act_bank]=act_row;
        open_banks[int'(act_rank)*16+int'(act_bank)]=1;act_pending=0;
        if(profile_i.mr27[0]) begin
          imt=int'(profile_i.mr27[5:1])*8;raa_max=imt*(2+2*int'(profile_i.mr27[7:6]));
          assert(raa[act_rank][act_bank%8]<raa_max) else $fatal(1,"CA crossed mandatory RAA maximum");
          raa[act_rank][act_bank%8]++;
        end
      end else if(ca_i.address[2:0]==7) begin
        act_pending=1;act_rank=1'(rank_value);act_bank=4'(bank_value);act_row={ca_i.address[6:3],ca_i.address[13:11],11'b0};
      end else if(ca_i.address[3:0]==12) begin
        // Synchronization/WS_OFF are not memory data commands.
        assert(ca_i.address[13:7]==0 && ca_i.address[6:4] inside {3'd1,3'd2,3'd4,3'd7})
          else $fatal(1,"unsupported CAS synchronization/WS_OFF operand");
      end else if(ca_i.address[2:0] inside {1,2,4,5,6} || ca_i.address[6:0]==24) begin
        assert(command_count<1024) else $fatal(1,"fixture command capacity");
        commands[command_count]='0;commands[command_count].at_ck=ck;commands[command_count].rank_index=1'(rank_value);
        commands[command_count].mrr=ca_i.address[6:0]==24;commands[command_count].mr_address=ca_i.address[13:7];
        commands[command_count].write_access=ca_i.address[2:0] inside {2,4,6};commands[command_count].bl32=ca_i.address[2:0] inside {4,5};
        commands[command_count].address=(64'(rank_value)<<30) | (64'(rows[rank_value][bank_value])<<15)
          | (64'(bank_value)<<11) | (64'({ca_i.address[6:4],ca_i.address[12:11],ca_i.address[3]})<<5);
        if(commands[command_count].mrr) begin
          assert(open_banks==0) else $fatal(1,"MRR without closed pages");
          if(commands[command_count].mr_address==43) begin
            assert(write_epoch_pending[rank_value] && ck-first_write_ck[rank_value]<=int'(profile_i.management_ck_mr43_poll_bound))
              else $fatal(1,"pin MR43 deadline rank=%0d age=%0d",rank_value,ck-first_write_ck[rank_value]);
            write_epoch_pending[rank_value]=0;last_mr43_ck[rank_value]=ck;mr43_count_o++;
          end else if(commands[command_count].mr_address==4) mr4_count_o++;
        end else begin
          assert(open_banks[rank_value*16+bank_value]) else $fatal(1,"CAS without ACT");
          if(commands[command_count].write_access && profile_i.write_link) begin
            assert(ck-last_mr43_ck[rank_value]>=int'(profile_i.dram_ck_tmrr)+int'(profile_i.dram_ck_tmrw)) else $fatal(1,"MR43 clear guard");
            if(!write_epoch_pending[rank_value]) first_write_ck[rank_value]=ck;
            write_epoch_pending[rank_value]=1;
          end
          if(ca_i.address[13]) open_banks[rank_value*16+bank_value]=0;
        end
        command_count++;
      end else if(ca_i.address[6:0]==120) begin
        if(ca_i.address[13]) for(int b=0;b<16;b++) open_banks[rank_value*16+b]=0;
        else open_banks[rank_value*16+bank_value]=0;
      end else if(ca_i.address[6:0]==56) begin
        if(ca_i.address[10]) rfm_count_o++;else refresh_count_o++;
        imt=int'(profile_i.mr27[5:1])*8;
        case(profile_i.mr57[1:0])
          0: raa_decrement=imt;1: raa_decrement=imt*3/2;2: raa_decrement=imt*2;default: raa_decrement=imt*4;
        endcase
        if(!ca_i.address[10]) raa_decrement=imt;
        for(int p=0;p<8;p++) if(ca_i.address[13] || p==int'(ca_i.address[9:7])) begin
          assert(!open_banks[rank_value*16+p] && !open_banks[rank_value*16+p+8]) else $fatal(1,"REF/RFM with open affected bank");
          raa[rank_value][p]=raa[rank_value][p]>raa_decrement ? raa[rank_value][p]-raa_decrement : 0;
        end
      end else if(ca_i.address[6:0]==112) zq_count_o++;
    end
    for(int r=0;r<2;r++) if(write_epoch_pending[r])
      assert(ck-first_write_ck[r]<=int'(profile_i.management_ck_mr43_poll_bound)) else $fatal(1,"missing pin MR43 rank=%0d age=%0d",r,ck-first_write_ck[r]);
    ck++;
  endtask
endmodule
