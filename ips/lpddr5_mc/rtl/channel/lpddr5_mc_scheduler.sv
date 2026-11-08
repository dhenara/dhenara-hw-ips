// CK-rate queue/timing owner. Selection enables affect new proposals only;
// accepted ACT and synchronization pairs always occupy the following CK.
module lpddr5_mc_scheduler #(
  parameter int QUEUE_DEPTH=64,
  parameter int CHANNEL_INDEX=0
) (
  input logic clk_ck_i, rst_ni, abort_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_config_t config_i,
  input logic admission_i, issue_enable_i, command_enable_i,
  input logic request_valid_i,
  output logic request_ready_o,
  input lpddr5_mc_contract_pkg::lpddr5_mc_granule_t request_i,
  output logic launch_valid_o,
  input logic launch_ready_i,
  output lpddr5_mc_contract_pkg::lpddr5_mc_granule_t launch_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_ca_t ca_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_event_t event_o,
  output logic sync_o, sync_write_o, sync_rank_o,
  input logic wck_disabled_i, wck_reusable_i, wck_rank_i, wck_write_i,
  output logic drained_o, pair_pending_o, fault_o,
  input logic maintenance_valid_i,
  input logic maintenance_rfm_i,
  input logic maintenance_rank_i, maintenance_all_i,
  input logic [2:0] maintenance_pair_i,
  input logic [1:0][7:0] activate_allowed_i,
  input logic close_i,
  output logic stop_requested_o,
  output logic maintenance_ready_o,
  output logic pages_closed_o, rows_closed_o,
  output logic [63:0] accepted_o, issued_o, replayed_o, max_wait_o
);
  import lpddr5_mc_contract_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  localparam int TOTAL=2*QUEUE_DEPTH;
  typedef enum logic [2:0] {OP_ACT, OP_PRE, OP_ACCESS} next_t;
  typedef enum logic [1:0] {PAIR_NONE, PAIR_ACT, PAIR_ACCESS} pair_t;
  typedef struct packed {
    logic valid;
    logic [63:0] order_number, accepted_ck;
    logic [15:0] age;
    lpddr5_mc_granule_t granule;
  } entry_t;
  entry_t [TOTAL-1:0] entries_q;
  logic [63:0] ck_q, order_q;
  logic [31:0] tick_q;
  logic [63:0] cap_epoch_q, current_cap_epoch;
  logic [32:0] read_used, write_used;
  logic [32:0] read_bytes_q, write_bytes_q;
  logic [2:0] phase_q;
  logic [31:0] open_q;
  logic [17:0] rows_q[32];
  logic [63:0] act_ready_q[32], pre_ready_q[32], access_ready_q[32];
  logic [63:0] rrd_ready_q[2], faw_q[2][4], column_ready_q[2][4];
  logic [63:0] last_masked_q[32];
  logic [31:0] has_masked_q;
  logic [63:0] refresh_ready_q[2], read_ready_q[2][4], write_ready_q;
  logic [63:0] opened_at_q[32], precharge_ready_q;
  logic [63:0] last_access_q[32];
  logic direction_q;
  logic [15:0] batch_q, hit_run_q;
  logic preferred, best_preferred, policy_direction, capped_miss, best_capped_miss;
  logic [15:0] batch_target;
  int expiry_bank,idle_bank,close_bank,write_occupancy;
  logic [1023:0] dq_calendar_q;
  logic pick_q;
  int pick_index_q;
  lpddr5_mc_tag_t pick_tag_q;
  logic [15:0] pick_generation_q;
  next_t pick_operation_q;
  pair_t pair_q;
  int pair_index_q;
  lpddr5_mc_granule_t pair_granule_q;
  int free_index, selected_index, issue_index, bank_index, rank_index, group_index;
  int maintenance_bank;
  next_t selected_operation, live_operation;
  logic selected, legal, free_found, hazard, hit, urgent, best_urgent;
  logic masked, opposite_eligible;
  logic candidate_eligible, candidate_cap, candidate_better;
  logic new_group, best_new_group;
  logic policy_waiting, capped_miss_waiting;
  logic miss_lock_q,miss_locked,locked_candidate,best_locked;
  int miss_index_q;
  lpddr5_mc_tag_t miss_tag_q;
  logic [15:0] miss_generation_q;
  logic do_act1, do_act2, do_pre, do_access, do_sync, do_refresh, do_maint_pre, do_expiry_pre, maint_pre_all;
  logic [63:0] best_order;
  logic [15:0] best_age;
  logic [1:0] best_qos;
  logic [1023:0] access_mask;
  logic [32:0] bytes_used, cap;

  logic [63:0] final_pin_ck;
  logic unused_config;
  assign unused_config=^{config_i,free_index,rank_index,bank_index};

  function automatic int bank_number(input lpddr5_mc_granule_t g);
    logic unused_granule;
    unused_granule=^g;
    return int'(g.rank_index)*16+int'({g.bank_group,g.bank});
  endfunction
  function automatic next_t required_operation(input lpddr5_mc_granule_t g);
    logic [4:0] b;
    b=5'(bank_number(g));
    return !open_q[b] ? OP_ACT : rows_q[b]==g.row ? OP_ACCESS : OP_PRE;
  endfunction
  function automatic logic overlap_blocked(input int index_value);
    for(int j=0;j<TOTAL;j++) begin
      if(entries_q[j].valid && entries_q[j].order_number<entries_q[index_value].order_number
          && entries_q[j].granule.rank_index==entries_q[index_value].granule.rank_index
          && entries_q[j].granule.bank_group==entries_q[index_value].granule.bank_group
          && entries_q[j].granule.bank==entries_q[index_value].granule.bank
          && entries_q[j].granule.row==entries_q[index_value].granule.row
          && entries_q[j].granule.column[5:1]==entries_q[index_value].granule.column[5:1]) return 1;
    end
    return 0;
  endfunction
  function automatic logic [1023:0] dq_mask(input lpddr5_mc_granule_t g, input int lead);
    logic [1023:0] mask_value;
    logic unused_granule;
    int first, span, beat_at;
    unused_granule=^g;
    first=lead*2*int'(config_i.profile.ratio)+int'(config_i.profile.phy_half_wck_command)
      +(g.is_write ? int'(config_i.profile.wl_ck) : int'(config_i.profile.rl_ck))*2*int'(config_i.profile.ratio)
      +(g.is_write ? int'(config_i.profile.phy_half_wck_dq_input) : int'(config_i.profile.phy_half_wck_dq_output));
    mask_value='0; span=g.is_64_bytes ? 32 : 16;
    for (int beat=0; beat<32; beat++) begin
      beat_at=first+beat+(config_i.profile.ratio==4 && g.is_64_bytes && beat>=16 ? 16 : 0);
      if (beat<span && beat_at<1024) mask_value[beat_at]=1;
    end
    return mask_value;
  endfunction
  function automatic logic command_legal(input int index_value, input next_t operation, input int lead);
    int b,bg;
    logic r;
    logic [2:0] pair_number;
    logic unused_index;
    logic mask_write;
    logic [63:0] at_ck;
    unused_index=^index_value;
    b=bank_number(entries_q[index_value].granule); r=1'(b/16); bg=(b%16)/4;
    pair_number=3'(b%8); at_ck=ck_q+64'(lead);
    mask_write=entries_q[index_value].granule.is_write
      && (entries_q[index_value].granule.is_64_bytes ? entries_q[index_value].granule.byte_enable!='1
      : entries_q[index_value].granule.byte_enable[31:0]!='1);
    case (operation)
      OP_ACT: return at_ck>=act_ready_q[b] && at_ck>=rrd_ready_q[r]
        && at_ck>=faw_q[r][0] && activate_allowed_i[r][pair_number];
      OP_PRE: return at_ck>=pre_ready_q[b] && at_ck>=precharge_ready_q;
      OP_ACCESS: begin
        if (at_ck<access_ready_q[b] || at_ck<column_ready_q[r][bg]
            || (entries_q[index_value].granule.is_write ? at_ck<write_ready_q : at_ck<read_ready_q[r][bg])) return 0;
        for (int other=0; other<32; other++) begin
          if (mask_write && has_masked_q[other]
              && at_ck-last_masked_q[other]<64'(other==b ? 16 : config_i.profile.ratio==2 || (other%16)/4==bg ? 4 : 2)) return 0;
        end
        return (dq_mask(entries_q[index_value].granule,lead) & dq_calendar_q)==0;
      end
      default: return 0;
    endcase
  endfunction

  assign pair_pending_o=pair_q!=PAIR_NONE;
  assign miss_locked=miss_lock_q && entries_q[miss_index_q].valid
    && entries_q[miss_index_q].granule.tag==miss_tag_q && entries_q[miss_index_q].granule.generation==miss_generation_q;
  assign rows_closed_o=open_q==0;
  assign launch_o=pair_q!=PAIR_NONE ? pair_granule_q : entries_q[pick_index_q].granule;
  always_comb begin
    expiry_bank=-1;idle_bank=-1;drained_o=pair_q==PAIR_NONE;pages_closed_o=open_q==0;
    for(int b=0;b<32;b++) begin
      if(ck_q<act_ready_q[b]) pages_closed_o=0;
      if(open_q[b] && ck_q+64'(config_i.profile.wl_ck)+64'(config_i.profile.dram_ck_twr)+32>=opened_at_q[b]+64'(config_i.profile.dram_ck_tras_max)
          && expiry_bank<0) expiry_bank=b;
      if(open_q[b] && config_i.page_idle_ck!=0 && ck_q-last_access_q[b]>=64'(config_i.page_idle_ck)
          && ck_q>=pre_ready_q[b] && idle_bank<0) idle_bank=b;
    end
    for(int i=0;i<TOTAL;i++) if(entries_q[i].valid) drained_o=0;
  end
  always_comb begin
    current_cap_epoch=ck_q / (config_i.bw_window==0 ? 64'd1 : 64'(config_i.bw_window));
    read_used=current_cap_epoch==cap_epoch_q ? read_bytes_q : 33'd0;
    write_used=current_cap_epoch==cap_epoch_q ? write_bytes_q : 33'd0;

    write_occupancy=0;
    for(int i=QUEUE_DEPTH;i<TOTAL;i++) if(entries_q[i].valid) write_occupancy++;
    batch_target=direction_q ? config_i.write_weight : config_i.read_weight;
    if(batch_target<(direction_q ? 16'(config_i.write_batch_min) : 16'(config_i.read_batch_min)))
      batch_target=(direction_q ? 16'(config_i.write_batch_min) : 16'(config_i.read_batch_min));
    if(direction_q && config_i.write_batch_max!=0 && batch_target>16'(config_i.write_batch_max))
      batch_target=16'(config_i.write_batch_max);

    policy_direction=batch_q>=batch_target ? !direction_q : direction_q;
    if(!direction_q && config_i.write_high!=0 && write_occupancy>=int'(config_i.write_high)
        && batch_q>=16'(config_i.read_batch_min)) policy_direction=1;
    if(direction_q && batch_q>=16'(config_i.write_batch_min)
        && (write_occupancy<=int'(config_i.write_low)
          || (config_i.write_batch_max!=0 && batch_q>=16'(config_i.write_batch_max)))) policy_direction=0;

    free_found=0;free_index=0;
    preferred=0; best_preferred=0;capped_miss=0;best_capped_miss=0;locked_candidate=0;best_locked=0;
    selected=0; selected_index=0; selected_operation=OP_ACT; best_order='1;
    best_urgent=0; best_age=0; best_qos=0;
    bank_index=0; rank_index=0; group_index=0; maintenance_bank=-1;
    hazard=0; hit=0; urgent=0; masked=0; opposite_eligible=0;
    candidate_eligible=0; candidate_cap=0; candidate_better=0; live_operation=OP_ACT;
    new_group=0;best_new_group=0;
    bytes_used=0; cap=0; final_pin_ck=0;
    for (int i=0; i<TOTAL; i++) begin
      if (!entries_q[i].valid && (i/QUEUE_DEPTH)==int'(request_i.is_write) && !free_found) begin
        free_found=1; free_index=i;
      end
    end
    // Reserve a requested turnaround while its timing matures. Continuing
    // to issue the old direction would move that deadline forever. Empty,
    // overlap-blocked or cap-disabled directions do not hold the batch.
    policy_waiting=0;capped_miss_waiting=0;
    for(int i=0;i<TOTAL;i++) if(entries_q[i].valid && !overlap_blocked(i)) begin
      bytes_used=entries_q[i].granule.is_write ? write_used : read_used;
      cap={1'b0,(entries_q[i].granule.is_write ? config_i.write_cap : config_i.read_cap)};
      candidate_cap=!config_i.caps_enable || (bytes_used+33'(entries_q[i].granule.is_64_bytes ? 64 : 32)<=cap
        && (!config_i.work_conserving || 34'(read_used)+34'(write_used)+34'(entries_q[i].granule.is_64_bytes ? 64 : 32)
          <=34'(config_i.read_cap)+34'(config_i.write_cap)));
      if(candidate_cap && (entries_q[i].granule.is_write ? config_i.write_weight!=0 : config_i.read_weight!=0)) begin
        if(entries_q[i].granule.is_write==policy_direction) policy_waiting=1;
        if(required_operation(entries_q[i].granule)!=OP_ACCESS && config_i.hit_cap!=0 && hit_run_q>=config_i.hit_cap)
          capped_miss_waiting=1;
      end
    end
    for (int i=0; i<TOTAL; i++) begin
      if (entries_q[i].valid) begin
        hazard=overlap_blocked(i);
        live_operation=required_operation(entries_q[i].granule);
        bytes_used=entries_q[i].granule.is_write ? write_used : read_used;
        cap={1'b0,(entries_q[i].granule.is_write ? config_i.write_cap : config_i.read_cap)};
        candidate_cap=!config_i.caps_enable || (bytes_used+33'(entries_q[i].granule.is_64_bytes ? 64 : 32)<=cap
        && (!config_i.work_conserving || 34'(read_used)+34'(write_used)+34'(entries_q[i].granule.is_64_bytes ? 64 : 32)
          <=34'(config_i.read_cap)+34'(config_i.write_cap)));
        if (!candidate_cap && cap!=0 && config_i.work_conserving) begin
          opposite_eligible=0;
          for (int j=0; j<TOTAL; j++)
            if (entries_q[j].valid && !overlap_blocked(j) && entries_q[j].granule.is_write!=entries_q[i].granule.is_write
                && (entries_q[j].granule.is_write ? config_i.write_cap!=0 : config_i.read_cap!=0)
                && command_legal(j,required_operation(entries_q[j].granule),1)) opposite_eligible=1;
          if (!opposite_eligible && 34'(read_used)+34'(write_used)
              +34'(entries_q[i].granule.is_64_bytes ? 64 : 32)
              <=34'(config_i.read_cap)+34'(config_i.write_cap)) candidate_cap=1;
        end
        urgent=entries_q[i].age>=(entries_q[i].granule.is_write ? config_i.write_urgent_age : config_i.read_urgent_age);
        candidate_eligible=!hazard && command_legal(i,live_operation,1) && candidate_cap
          && (urgent || ((entries_q[i].granule.is_write ? config_i.write_weight!=0 : config_i.read_weight!=0)
            && (miss_locked ? i==miss_index_q : ((!policy_waiting || capped_miss_waiting || entries_q[i].granule.is_write==policy_direction)
              && (!capped_miss_waiting || live_operation!=OP_ACCESS)))));
        hit=live_operation==OP_ACCESS; preferred=entries_q[i].granule.is_write==policy_direction;
        new_group=live_operation==OP_ACT
          && (open_q & (32'hf << (4*(bank_number(entries_q[i].granule)/4))))==0;
        capped_miss=!hit && config_i.hit_cap!=0 && hit_run_q>=config_i.hit_cap;
        locked_candidate=miss_locked && i==miss_index_q;
        candidate_better=!selected;
        if(selected) begin
          if(urgent!=best_urgent) candidate_better=urgent;
          else if(urgent) candidate_better=entries_q[i].age>best_age
            || (entries_q[i].age==best_age && entries_q[i].order_number<best_order);
          else if(locked_candidate!=best_locked) candidate_better=locked_candidate;
          else if(capped_miss!=best_capped_miss) candidate_better=capped_miss;
          else if(preferred!=best_preferred) candidate_better=preferred;
          else if(entries_q[i].granule.qos_class!=best_qos) candidate_better=entries_q[i].granule.qos_class>best_qos;
          else if(hit!=(selected_operation==OP_ACCESS)) candidate_better=hit;
          // Spread otherwise equal nonurgent misses across bank groups.
          // Opening only one group after closure can strand the next CK/4
          // access slot on tCCD even while another group's DQ slot is free.
          else if(new_group!=best_new_group) candidate_better=new_group;
          else candidate_better=entries_q[i].order_number<best_order;
        end
        if(candidate_eligible && candidate_better) begin
          selected=1; selected_index=i; selected_operation=live_operation;
          best_urgent=urgent; best_age=entries_q[i].age; best_order=entries_q[i].order_number;
          best_qos=entries_q[i].granule.qos_class; best_preferred=preferred;
          best_capped_miss=capped_miss;
          best_locked=locked_candidate;
          best_new_group=new_group;
        end
      end
    end
    request_ready_o=admission_i && free_found && !fault_o && order_q!='1
      && request_i.channel_index==1'(CHANNEL_INDEX) && int'(request_i.rank_index)<int'(config_i.profile.ranks)
      && request_i.row[17:15]==0 && request_i.column[11:6]==0
      && (!request_i.is_64_bytes || request_i.column[0]==0)
      && !(request_i.is_write && request_i.is_64_bytes && request_i.byte_enable!='1);
    ca_o='0; launch_valid_o=0; sync_o=0; sync_rank_o=0; sync_write_o=0;
    do_act1=0; do_act2=0; do_pre=0; do_access=0; do_sync=0; do_refresh=0; do_maint_pre=0; do_expiry_pre=0; maint_pre_all=close_i || maintenance_all_i; stop_requested_o=close_i || maintenance_valid_i;
    maintenance_ready_o=0;
    issue_index=pick_index_q; legal=0; access_mask=0;close_bank=expiry_bank;
    if (pair_q!=PAIR_NONE) begin
      issue_index=pair_index_q;
      if (pair_q==PAIR_ACT) begin do_act2=1; ca_o.address=ca_activate(pair_granule_q.row,{pair_granule_q.bank_group,pair_granule_q.bank},1); end
      else begin do_access=1; launch_valid_o=1;
        masked=pair_granule_q.is_write && (pair_granule_q.is_64_bytes ? pair_granule_q.byte_enable!='1 : pair_granule_q.byte_enable[31:0]!='1);
        ca_o.address=ca_access(pair_granule_q.is_write,pair_granule_q.is_64_bytes,masked,
          {pair_granule_q.bank_group,pair_granule_q.bank},pair_granule_q.column[5:0],config_i.page_policy==1);
      end
      ca_o.cs=2'b1<<pair_granule_q.rank_index;
    end else if (command_enable_i && (maintenance_valid_i || close_i)) begin
      for (int b=0; b<16; b++)
        if (open_q[int'(maintenance_rank_i)*16+b] && (close_i || maintenance_all_i || b%8==int'(maintenance_pair_i)) && maintenance_bank<0) maintenance_bank=int'(maintenance_rank_i)*16+b;
      if (maintenance_bank>=0) begin
        legal=ck_q>=precharge_ready_q;
        for(int b=0;b<16;b++) begin
          if(open_q[int'(maintenance_rank_i)*16+b]
              && (maint_pre_all || b%8==int'(maintenance_pair_i)) && ck_q<pre_ready_q[int'(maintenance_rank_i)*16+b]) legal=0;
          // PREab also reaches banks closed by an earlier PREpb or auto-PRE.
          // Their recovery survives open_q clearing, including a later tRC
          // fence; tPPD alone cannot make the rank-wide command legal.
          if(maint_pre_all && !open_q[int'(maintenance_rank_i)*16+b]
              && ck_q<act_ready_q[int'(maintenance_rank_i)*16+b]) legal=0;
        end
        if (legal) begin
          do_maint_pre=1; ca_o.address=ca_maintenance(1,0,maint_pre_all,4'(maintenance_bank));
          ca_o.cs=2'b1<<maintenance_rank_i;
        end
      end else begin
        legal=1;
        for (int b=0; b<16; b++)
          if ((maintenance_all_i || b%8==int'(maintenance_pair_i)) && ck_q<act_ready_q[int'(maintenance_rank_i)*16+b]) legal=0;
        if (legal && ck_q>=refresh_ready_q[maintenance_rank_i] && wck_disabled_i && !close_i) begin
          do_refresh=1; ca_o.address=ca_maintenance(0,maintenance_rfm_i,maintenance_all_i,{1'b0,maintenance_pair_i});
          ca_o.cs=2'b1<<maintenance_rank_i; maintenance_ready_o=1;
        end
      end
    end else if(command_enable_i && expiry_bank>=0 && !fault_o) begin
      if(ck_q>=pre_ready_q[expiry_bank] && ck_q>=precharge_ready_q) begin
        do_expiry_pre=1; ca_o.cs=2'b1<<(expiry_bank/16); ca_o.address=ca_maintenance(1,0,0,4'(expiry_bank));
      end
    end else if(command_enable_i && idle_bank>=0 && !selected && !pick_q && !fault_o && ck_q>=precharge_ready_q) begin
      // Idle closure uses otherwise unused CA slots. Legal queued work and
      // mandatory pairs retain priority; tRAS(max) closure above cannot wait.
      do_expiry_pre=1;close_bank=idle_bank;
      ca_o.cs=2'b1<<(idle_bank/16);ca_o.address=ca_maintenance(1,0,0,4'(idle_bank));
    end else if (pick_q && command_enable_i && issue_enable_i && !fault_o) begin
      legal=entries_q[pick_index_q].valid && entries_q[pick_index_q].granule.tag==pick_tag_q
        && entries_q[pick_index_q].granule.generation==pick_generation_q
        && required_operation(entries_q[pick_index_q].granule)==pick_operation_q
        && command_legal(pick_index_q,pick_operation_q,(pick_operation_q==OP_ACCESS && wck_disabled_i) ? 1 : 0);
      if (legal && pick_operation_q==OP_ACCESS && config_i.caps_enable) begin
        bytes_used=launch_o.is_write ? write_used : read_used;
        cap={1'b0,(launch_o.is_write ? config_i.write_cap : config_i.read_cap)};
        opposite_eligible=0;
        for (int j=0; j<TOTAL; j++)
          if (entries_q[j].valid && !overlap_blocked(j) && entries_q[j].granule.is_write!=launch_o.is_write
              && (entries_q[j].granule.is_write ? config_i.write_cap!=0 : config_i.read_cap!=0)
              && command_legal(j,required_operation(entries_q[j].granule),0)) opposite_eligible=1;
        if (bytes_used+33'(launch_o.is_64_bytes ? 64 : 32)>cap
            && !(config_i.work_conserving && cap!=0 && !opposite_eligible
              && 34'(read_used)+34'(write_used)+34'(launch_o.is_64_bytes ? 64 : 32)
                <=34'(config_i.read_cap)+34'(config_i.write_cap))) legal=0;
      end
      if(legal && pick_operation_q==OP_ACCESS && config_i.caps_enable && config_i.work_conserving
          && 34'(read_used)+34'(write_used)+34'(launch_o.is_64_bytes ? 64 : 32)
            >34'(config_i.read_cap)+34'(config_i.write_cap)) legal=0;
      if (legal) begin
        ca_o.cs=2'b1<<launch_o.rank_index;
        if (pick_operation_q==OP_ACT) begin do_act1=1; ca_o.address=ca_activate(launch_o.row,{launch_o.bank_group,launch_o.bank},0); end
        else if (pick_operation_q==OP_PRE) begin do_pre=1; ca_o.address=ca_maintenance(1,0,0,{launch_o.bank_group,launch_o.bank}); end
        else if (launch_ready_i && wck_disabled_i) begin
          do_sync=1; sync_o=1; sync_rank_o=launch_o.rank_index; sync_write_o=launch_o.is_write;
          ca_o.address=ca_cas(launch_o.is_write ? 3'd1 : 3'd2);
        end else if (launch_ready_i && wck_reusable_i && wck_rank_i==launch_o.rank_index && wck_write_i==launch_o.is_write) begin
          do_access=1; launch_valid_o=1;
          masked=launch_o.is_write && (launch_o.is_64_bytes ? launch_o.byte_enable!='1 : launch_o.byte_enable[31:0]!='1);
          // Masked BL32 is expanded by an owning adapter before this queue.
          ca_o.address=ca_access(launch_o.is_write,launch_o.is_64_bytes,masked,
            {launch_o.bank_group,launch_o.bank},launch_o.column[5:0],config_i.page_policy==1);
        end else begin
          ca_o.cs=0;
          if(!wck_disabled_i && (!wck_reusable_i || wck_rank_i!=launch_o.rank_index || wck_write_i!=launch_o.is_write)) stop_requested_o=1;
        end
      end
    end
    if (do_access) access_mask=dq_mask(launch_o,0);
    bank_index=bank_number(launch_o); rank_index=int'(launch_o.rank_index); group_index=int'(launch_o.bank_group);
    final_pin_ck=ck_q+64'(config_i.profile.management_ck_tctrl_delay)
      +(launch_o.is_write ? 64'(config_i.profile.wl_ck) : 64'(config_i.profile.rl_ck))
      +64'(launch_o.is_64_bytes ? (config_i.profile.ratio==4 ? 6 : 8) : (config_i.profile.ratio==4 ? 2 : 4))+1;
  end

  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if (!rst_ni) begin
      ck_q<=0; order_q<=0; tick_q<=0; cap_epoch_q<=0; read_bytes_q<=0; write_bytes_q<=0;
      phase_q<=0; open_q<=0; dq_calendar_q<=0; precharge_ready_q<=0; write_ready_q<=0; direction_q<=0; batch_q<=0;hit_run_q<=0;
      pick_q<=0; pick_index_q<=0; pick_tag_q<=0; pick_generation_q<=0; pick_operation_q<=OP_ACT;
      pair_q<=PAIR_NONE; pair_index_q<=0; pair_granule_q<='0; has_masked_q<=0;
      miss_lock_q<=0;miss_index_q<=0;miss_tag_q<=0;miss_generation_q<=0;
      fault_o<=0; max_wait_o<=0; accepted_o<=0; issued_o<=0; replayed_o<=0; event_o<='0;
      for (int i=0; i<TOTAL; i++) entries_q[i]<='0;
      for (int b=0; b<32; b++) begin rows_q[b]<=0; act_ready_q[b]<=0; pre_ready_q[b]<=0; access_ready_q[b]<=0; last_masked_q[b]<=0; opened_at_q[b]<=0;last_access_q[b]<=0; end
      for (int r=0; r<2; r++) begin
        rrd_ready_q[r]<=0; refresh_ready_q[r]<=0;
        for (int j=0; j<4; j++) begin faw_q[r][j]<=0; column_ready_q[r][j]<=0; read_ready_q[r][j]<=0; end
      end
    end else if (abort_i) begin
      ck_q<=0; order_q<=0; tick_q<=0; cap_epoch_q<=0; read_bytes_q<=0; write_bytes_q<=0;
      phase_q<=0; open_q<=0; dq_calendar_q<=0; precharge_ready_q<=0; write_ready_q<=0; direction_q<=0; batch_q<=0;hit_run_q<=0; pick_q<=0; pair_q<=PAIR_NONE;
      fault_o<=0; has_masked_q<=0; event_o<='0;
      miss_lock_q<=0;miss_index_q<=0;miss_tag_q<=0;miss_generation_q<=0;
      for (int i=0; i<TOTAL; i++) entries_q[i]<='0;
      for (int b=0; b<32; b++) begin rows_q[b]<=0; act_ready_q[b]<=0; pre_ready_q[b]<=0; access_ready_q[b]<=0; last_masked_q[b]<=0; opened_at_q[b]<=0;last_access_q[b]<=0; end
      for (int r=0; r<2; r++) begin
        rrd_ready_q[r]<=0; refresh_ready_q[r]<=0;
        for (int j=0; j<4; j++) begin faw_q[r][j]<=0; column_ready_q[r][j]<=0; read_ready_q[r][j]<=0; end
      end
    end else begin
      ck_q<=ck_q+1; event_o<='0;
      if(!miss_locked) miss_lock_q<=0;
      dq_calendar_q<=(dq_calendar_q | access_mask)>>(2*int'(config_i.profile.ratio));
      phase_q<=phase_q+1>=config_i.service_div ? 3'd0 : phase_q+1;
      tick_q<=tick_q+1>=32'(config_i.age_tick_div) ? 0 : tick_q+1;
      for (int i=0; i<TOTAL; i++)
        if (entries_q[i].valid && tick_q+1>=32'(config_i.age_tick_div) && entries_q[i].age!='1) entries_q[i].age<=entries_q[i].age+1;
      if (request_valid_i && request_ready_o) begin
        entries_q[free_index].valid<=1; entries_q[free_index].order_number<=order_q;
        entries_q[free_index].accepted_ck<=ck_q; entries_q[free_index].age<=0; entries_q[free_index].granule<=request_i;
        order_q<=order_q+1;
        if (accepted_o!='1) accepted_o<=accepted_o+1;
      end
      pick_q<=0;
      if (phase_q==0 && selected && pair_q==PAIR_NONE && !maintenance_valid_i && !close_i && expiry_bank<0 && issue_enable_i) begin
        pick_q<=1; pick_index_q<=selected_index; pick_tag_q<=entries_q[selected_index].granule.tag;
        pick_generation_q<=entries_q[selected_index].granule.generation; pick_operation_q<=selected_operation;
      end
      if (pick_q && !(do_act1 || do_pre || do_access || do_sync) && pair_q==PAIR_NONE)
        if (replayed_o!='1) replayed_o<=replayed_o+1;
      if (do_act1 || do_sync) begin
        pair_q<=do_act1 ? PAIR_ACT : PAIR_ACCESS; pair_index_q<=issue_index;
        pair_granule_q<=launch_o; pick_q<=0;
      end else if (pair_q!=PAIR_NONE) pair_q<=PAIR_NONE;
      // Retain the chosen miss across PRE/ACT/access. Re-arbitrating after
      // PRE would let high-QoS hits reopen the old row and defeat HIT_CAP.
      if(!miss_locked && capped_miss_waiting && (do_pre || do_act1)) begin
        miss_lock_q<=1;miss_index_q<=issue_index;miss_tag_q<=launch_o.tag;miss_generation_q<=launch_o.generation;
      end
      if (do_act2) begin
        open_q[bank_index]<=1; rows_q[bank_index]<=launch_o.row; opened_at_q[bank_index]<=ck_q;
        last_access_q[bank_index]<=ck_q;hit_run_q<=0;
        access_ready_q[bank_index]<=ck_q+64'(config_i.profile.dram_ck_trcd);
        pre_ready_q[bank_index]<=ck_q+64'(config_i.profile.dram_ck_tras_min);
        act_ready_q[bank_index]<=ck_q+64'(config_i.profile.dram_ck_trc);
        rrd_ready_q[rank_index]<=ck_q+64'(config_i.profile.dram_ck_trrd);
        for (int j=0; j<3; j++) faw_q[rank_index][j]<=faw_q[rank_index][j+1];
        faw_q[rank_index][3]<=ck_q+64'(config_i.profile.dram_ck_tfaw);
      end
      if (do_pre) begin
        open_q[bank_index]<=0; precharge_ready_q<=ck_q+64'(config_i.profile.dram_ck_tppd);
        if(act_ready_q[bank_index]<ck_q+64'(config_i.profile.dram_ck_trp_pb)) act_ready_q[bank_index]<=ck_q+64'(config_i.profile.dram_ck_trp_pb);
      end
      if (do_access) begin
        if(miss_locked && issue_index==miss_index_q) miss_lock_q<=0;
        if (!launch_ready_i) fault_o<=1;
        entries_q[issue_index].valid<=0; pick_q<=0;
        if(ck_q-entries_q[issue_index].accepted_ck>max_wait_o) max_wait_o<=ck_q-entries_q[issue_index].accepted_ck;
        if (issued_o!='1) issued_o<=issued_o+1;
        if(launch_o.is_write!=direction_q) begin direction_q<=launch_o.is_write; batch_q<=1; end
        else if(batch_q!='1) batch_q<=batch_q+1;
        last_access_q[bank_index]<=ck_q;
        if(hit_run_q!='1) hit_run_q<=hit_run_q+1;
        cap_epoch_q<=current_cap_epoch;
        read_bytes_q<=read_used+(launch_o.is_write ? 33'd0 : 33'(launch_o.is_64_bytes ? 64 : 32));
        write_bytes_q<=write_used+(launch_o.is_write ? 33'(launch_o.is_64_bytes ? 64 : 32) : 33'd0);
        for (int bg=0; bg<4; bg++) column_ready_q[rank_index][bg]<=ck_q+64'(config_i.profile.ratio==2 ? (launch_o.is_64_bytes ? 8 : 4) : bg==group_index ? (launch_o.is_64_bytes ? 8 : 4) : 2);
        if (launch_o.is_write) begin
          for(int r=0;r<2;r++) for(int bg=0;bg<4;bg++)
            read_ready_q[r][bg]<=final_pin_ck+(bg==group_index ? 64'(config_i.profile.dram_ck_twtr_l) : 64'(config_i.profile.dram_ck_twtr_s));
          if (pre_ready_q[bank_index]<final_pin_ck+64'(config_i.profile.dram_ck_twr)) pre_ready_q[bank_index]<=final_pin_ck+64'(config_i.profile.dram_ck_twr);
          if (masked) begin has_masked_q[bank_index]<=1; last_masked_q[bank_index]<=ck_q; end
        end else begin
          // A zero CSR keeps the derived pin-safe bound. Larger software
          // minima extend turnaround from the read command's CK origin.
          write_ready_q<=final_pin_ck+2 > ck_q+64'(config_i.read_to_write_min_ck)
            ? final_pin_ck+2 : ck_q+64'(config_i.read_to_write_min_ck);
          if (pre_ready_q[bank_index]<final_pin_ck+64'(config_i.profile.dram_ck_trbtp)) pre_ready_q[bank_index]<=final_pin_ck+64'(config_i.profile.dram_ck_trbtp);
        end
        if (config_i.page_policy==1) begin
          open_q[bank_index]<=0; if(act_ready_q[bank_index]<final_pin_ck+(launch_o.is_write ? 64'(config_i.profile.dram_ck_twr) : 64'(config_i.profile.dram_ck_trbtp))+64'(config_i.profile.dram_ck_trp_pb)) act_ready_q[bank_index]<=final_pin_ck+(launch_o.is_write ? 64'(config_i.profile.dram_ck_twr) : 64'(config_i.profile.dram_ck_trbtp))+64'(config_i.profile.dram_ck_trp_pb);
        end
      end
      if (do_maint_pre || do_expiry_pre) begin
        precharge_ready_q<=ck_q+64'(config_i.profile.dram_ck_tppd);
        for(int b=0;b<32;b++) if((do_maint_pre && maint_pre_all && b/16==int'(maintenance_rank_i))
            || b==(do_expiry_pre ? close_bank : maintenance_bank)) begin
          open_q[b]<=0;
          if(act_ready_q[b]<ck_q+(do_maint_pre && maint_pre_all ? 64'(config_i.profile.dram_ck_trp_ab) : 64'(config_i.profile.dram_ck_trp_pb)))
            act_ready_q[b]<=ck_q+(do_maint_pre && maint_pre_all ? 64'(config_i.profile.dram_ck_trp_ab) : 64'(config_i.profile.dram_ck_trp_pb));
        end
      end
      for(int b=0;b<32;b++) if(open_q[b] && ck_q>opened_at_q[b]+64'(config_i.profile.dram_ck_tras_max)) fault_o<=1;
      if (do_refresh) begin
        refresh_ready_q[maintenance_rank_i]<=ck_q+(maintenance_all_i
          ? (maintenance_rfm_i ? 64'(config_i.profile.dram_ck_trfm_ab) : 64'(config_i.profile.dram_ck_trfc_ab))
          : 64'(config_i.profile.dram_ck_tpbr2pbr));
        for (int b=0; b<16; b++) begin
          if(maintenance_all_i || b%8==int'(maintenance_pair_i))
            act_ready_q[int'(maintenance_rank_i)*16+b]<=ck_q+(maintenance_rfm_i
              ? (maintenance_all_i ? 64'(config_i.profile.dram_ck_trfm_ab) : 64'(config_i.profile.dram_ck_trfm_pb))
              : (maintenance_all_i ? 64'(config_i.profile.dram_ck_trfc_ab) : 64'(config_i.profile.dram_ck_trfc_pb)));
          else if(act_ready_q[int'(maintenance_rank_i)*16+b]<ck_q+64'(config_i.profile.dram_ck_tpbr2act))
            act_ready_q[int'(maintenance_rank_i)*16+b]<=ck_q+64'(config_i.profile.dram_ck_tpbr2act);
        end
      end
      if (do_act1 || do_act2 || do_pre || do_access || do_sync) begin
        event_o.tag<=launch_o.tag; event_o.generation<=launch_o.generation; event_o.queue_index<=6'(issue_index%QUEUE_DEPTH);
        event_o.issued<=do_access; event_o.row_opened<=do_act2; event_o.row_closed<=do_pre;
        event_o.rank_index<=launch_o.rank_index; event_o.bank<={launch_o.bank_group,launch_o.bank}; event_o.row<=launch_o.row;
      end
    end
  end
endmodule
