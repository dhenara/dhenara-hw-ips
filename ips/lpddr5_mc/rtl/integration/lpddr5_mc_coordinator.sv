// Common-CK control owner. Its inputs are coherent crossed commands/configuration
// and actual host/channel witnesses. No queue-empty proxy authorizes recovery.
module lpddr5_mc_coordinator #(
  parameter int CHANNELS=2,
  parameter int COMMAND_TIMEOUT=65536
) (
  input logic clk_i,rst_ni,
  input logic cold_rst_ni,
  input logic command_valid_i,
  output logic command_ready_o,
  input lpddr5_mc_apb_pkg::lpddr5_mc_control_command_t command_i,
  input logic publication_busy_i,external_fault_i,control_fault_i,
  input logic [CHANNELS-1:0] management_request_i,
  input logic wake_request_i,
  input logic configuration_valid_i,
  output logic configuration_ready_o,
  input lpddr5_mc_host_pkg::lpddr5_mc_host_config_t configuration_i,
  output logic host_config_valid_o,
  input logic host_config_ready_i,host_config_valid_i,
  output lpddr5_mc_host_pkg::lpddr5_mc_host_config_t host_config_o,
  input logic host_settled_i,host_initialized_i,host_rearm_ready_i,host_fault_i,
  output logic host_enable_o,host_fatal_o,host_recover_o,host_rearm_o,
  input lpddr5_mc_channel_pkg::lpddr5_mc_channel_status_t [CHANNELS-1:0] status_i,
  output logic [CHANNELS-1:0] command_valid_o,
  input logic [CHANNELS-1:0] command_ready_i,command_rejected_i,
  output lpddr5_mc_channel_pkg::lpddr5_mc_channel_command_t [CHANNELS-1:0] command_o,
  output logic [CHANNELS-1:0] train_ack_o,perf_snapshot_o,
  input logic [CHANNELS-1:0] perf_ready_i,
  output logic [CHANNELS-1:0][31:0] train_token_o,
  output logic destructive_reset_request_o,
  input logic [CHANNELS-1:0] destructive_reset_confirmed_i,
  output logic [CHANNELS-1:0] channel_reset_confirmed_o,
  output logic force_channel_fault_o,fault_o,rejected_o,recovery_complete_o,
  output logic [31:0] boot_epoch_o,config_epoch_o,
  output logic profile_valid_o,trained_o,train_request_o,quiesced_o,trusted_o,
  output logic [3:0] state_o
);
  import lpddr5_mc_apb_metadata_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  typedef enum logic [3:0] {
    IDLE,SEND,WAIT_ENABLE,WAIT_QUIESCE,SETTLE,RESET_WAIT,
    SEND_RECOVER,WAIT_BOOT,RECOVER_HOST,SEND_PERF,WAIT_SLEEP,SLEEP,SEND_WAKE,WAIT_WAKE
  } state_t;
  state_t state_q;
  lpddr5_mc_channel_command_t operation_q;
  logic [CHANNELS-1:0] pending_q,reset_low_q,reset_confirmed_q;
  logic requested_enable_q,fault_q,rearm_sent_q,rearm_low_seen_q,recover_sent_q,host_monitored_q;
  logic host_epoch_initialized;
  logic [31:0] sequence_q,recovery_boot_q,timer_q;
  logic [31:0] installed_boot_q,installed_epoch_q,idle_q;
  logic [CHANNELS-1:0] quiet_mask,power_down_due,self_refresh_due,sleep_mask_q,sleeping_mask;
  logic all_runnable,sleep_settled,sleep_quiet;
  logic all_quiet,all_run,all_failed,all_boot,all_reset,epochs_equal,configuration_match;
  logic channel_fault,normal_state,recovering,control_fault_seen_q,configuration_current;
  logic unused_command;
  assign unused_command=^{command_i,status_i};
  always_comb begin
    quiet_mask=0;power_down_due=0;self_refresh_due=0;sleeping_mask=0;
    all_runnable=1;sleep_settled=1;sleep_quiet=1;
    epochs_equal=1;all_quiet=1;all_run=1;all_failed=1;all_boot=1;all_reset=1;
    trained_o=1;trusted_o=1;train_request_o=1;channel_fault=0;
    configuration_match=host_config_valid_i;
    boot_epoch_o=status_i[0].boot_epoch;config_epoch_o=installed_epoch_q;
    for(int c=0;c<CHANNELS;c++) begin
      all_runnable &= status_i[c].state inside {1,2};
      quiet_mask[c]=status_i[c].state==1;
      power_down_due[c]=status_i[c].power_policy==1 && status_i[c].power_down_idle_ck!=0 && idle_q>=status_i[c].power_down_idle_ck;
      self_refresh_due[c]=status_i[c].power_policy==2 && status_i[c].self_refresh_idle_ck!=0 && idle_q>=status_i[c].self_refresh_idle_ck;
      sleeping_mask[c]=sleep_mask_q[c] && command_ready_i[c] && status_i[c].state inside {3,4};
      if(sleep_mask_q[c]) begin
        sleep_settled &= sleeping_mask[c] || status_i[c].state==1;
        sleep_quiet &= status_i[c].state==1;
      end
      epochs_equal &= status_i[c].boot_epoch==boot_epoch_o;
      all_quiet &= status_i[c].state==1 && status_i[c].drained;
      all_run &= status_i[c].state==2;
      all_reset &= status_i[c].state==0;
      all_failed &= status_i[c].state==5 && status_i[c].fault;
      all_boot &= status_i[c].state==0 && !status_i[c].fault
        && status_i[c].boot_epoch==recovery_boot_q+1 && !status_i[c].config_valid;
      trained_o &= status_i[c].train_done;
      trusted_o &= status_i[c].memory_trusted;
      train_request_o &= status_i[c].train_request;
      channel_fault |= status_i[c].fault;
      configuration_match &= status_i[c].config_valid && status_i[c].config_epoch==installed_epoch_q
        && status_i[c].boot_epoch==installed_boot_q;
    end
    trained_o &= epochs_equal;trusted_o &= epochs_equal;train_request_o &= epochs_equal;
    configuration_current=epochs_equal;
    for(int c=0;c<CHANNELS;c++) configuration_current &= status_i[c].config_valid
      && configuration_i.boot_epoch==status_i[c].boot_epoch && configuration_i.config_epoch==status_i[c].config_epoch;
    normal_state=state_q inside {IDLE,SEND,WAIT_ENABLE,WAIT_QUIESCE,SEND_PERF,WAIT_SLEEP,SLEEP,SEND_WAKE,WAIT_WAKE};
    recovering=state_q inside {SETTLE,RESET_WAIT,SEND_RECOVER,WAIT_BOOT,RECOVER_HOST};
    fault_o=fault_q || external_fault_i || channel_fault;
    profile_valid_o=configuration_match && epochs_equal && !fault_o;
  end
  assign perf_snapshot_o=state_q==SEND_PERF ? pending_q & perf_ready_i : '0;
  assign quiesced_o=all_quiet && host_settled_i && !host_enable_o;
  assign state_o=fault_o ? 4'd5 : all_run && host_enable_o ? 4'd2 :
    all_quiet ? 4'd1 : all_reset ? 4'd0 : 4'd3;
  // Close source admission before channel drain starts, including unsolicited
  // PHY management. The channel keeps accepted CDC work live through DRAIN.
  assign host_epoch_initialized=rearm_sent_q && rearm_low_seen_q && host_initialized_i && !host_rearm_o;
  assign host_enable_o=requested_enable_q && host_epoch_initialized && all_run && profile_valid_o && trained_o && trusted_o
    && !(|management_request_i) && !publication_busy_i && !external_fault_i && !fault_q
    && !(state_q inside {SEND,WAIT_SLEEP,SLEEP,SEND_WAKE,WAIT_WAKE});
  assign host_fatal_o=(fault_q || external_fault_i || channel_fault)
    && !(state_q inside {SEND_RECOVER,WAIT_BOOT,RECOVER_HOST});
  assign force_channel_fault_o=host_fatal_o;
  assign host_config_o=configuration_i;
  assign host_config_valid_o=configuration_valid_i && state_q==IDLE && !requested_enable_q
    && host_settled_i && !fault_o && !publication_busy_i && configuration_current;
  assign configuration_ready_o=state_q==IDLE && !requested_enable_q && host_settled_i
    && !fault_o && !publication_busy_i && (configuration_current ? host_config_ready_i : 1'b1);
  assign command_ready_o=state_q==IDLE && !configuration_valid_i && !publication_busy_i;
  assign destructive_reset_request_o=state_q==RESET_WAIT;
  for(genvar c=0;c<CHANNELS;c++) begin : destinations
    assign command_valid_o[c]=(state_q==SEND || state_q==SEND_RECOVER || state_q==SEND_WAKE) && pending_q[c];
    assign command_o[c]=state_q==SEND_RECOVER ? CH_CMD_RECOVER : state_q==SEND_WAKE ? CH_CMD_WAKE : operation_q;
    assign channel_reset_confirmed_o[c]=state_q==SEND_RECOVER && pending_q[c] && reset_confirmed_q[c];
  end
  // Preserve fault observation across core reset until explicit recovery.
  always_ff @(posedge clk_i or negedge cold_rst_ni) begin
    if(!cold_rst_ni) host_monitored_q<=0;
    else begin
      if(host_initialized_i || host_rearm_o) host_monitored_q<=1;
      if(state_q==RECOVER_HOST && recover_sent_q && !host_fault_i) host_monitored_q<=0;
    end
  end
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if(!rst_ni) begin
      state_q<=IDLE;operation_q<=CH_CMD_NONE;pending_q<=0;reset_low_q<=0;reset_confirmed_q<=0;
      requested_enable_q<=0;fault_q<=0;control_fault_seen_q<=0;rearm_sent_q<=0;rearm_low_seen_q<=0;recover_sent_q<=0;
      sequence_q<=0;recovery_boot_q<=0;timer_q<=0;installed_boot_q<=0;installed_epoch_q<=0;idle_q<=0;sleep_mask_q<=0;
      host_recover_o<=0;host_rearm_o<=0;train_ack_o<=0;train_token_o<=0;
      rejected_o<=0;recovery_complete_o<=0;
    end else begin
      control_fault_seen_q<=control_fault_i;
      host_recover_o<=0;host_rearm_o<=0;train_ack_o<=0;
      rejected_o<=0;recovery_complete_o<=0;
      if(state_q==IDLE || state_q==SLEEP) timer_q<=0;else timer_q<=timer_q+1;
      if(!host_enable_o || !host_settled_i || wake_request_i) idle_q<=0;
      else if(idle_q!='1) idle_q<=idle_q+1;
      if(host_config_valid_o && host_config_ready_i) begin
        installed_boot_q<=configuration_i.boot_epoch;installed_epoch_q<=configuration_i.config_epoch;
        rearm_sent_q<=0;rearm_low_seen_q<=0;
      end else if(rearm_sent_q && !host_initialized_i) rearm_low_seen_q<=1;
      // An initialized source can still hold the previous configuration. Send
      // one rearm for every installed image while disabled and fully settled.
      // The registered pulse is consumed on the next edge; the old high level
      // must fall and then return before ENABLE can use the new source epoch.
      if(state_q==IDLE && !requested_enable_q && !configuration_valid_i
          && profile_valid_o && host_settled_i && host_rearm_ready_i && !rearm_sent_q) begin
        host_rearm_o<=1;rearm_sent_q<=1;
      end
      case(state_q)
        IDLE: begin
          if(command_valid_i && command_ready_o) begin
            if(command_i.sequence_number<=sequence_q || !epochs_equal || command_i.boot_epoch!=boot_epoch_o) rejected_o<=1;
            else begin
              sequence_q<=command_i.sequence_number;
              if(command_i.register_index==10'(INDEX_CONTROL)) begin
                case(command_i.command_mask)
                  32'd1: begin
                    if(!profile_valid_o || !trained_o || !trusted_o || !host_epoch_initialized || fault_o) rejected_o<=1;
                    else begin operation_q<=CH_CMD_ENABLE;pending_q<='1;state_q<=SEND;end
                  end
                  32'd2: begin
                    requested_enable_q<=0;operation_q<=CH_CMD_QUIESCE;pending_q<='1;state_q<=SEND;
                  end
                  32'd4: begin
                    // Boot follows fresh PHY state/profile/training inputs;
                    // INIT cannot manufacture any of those attestations.
                    if(!host_settled_i || trusted_o || fault_o) rejected_o<=1;
                  end
                  32'd8: begin
                    if(!fault_o || boot_epoch_o=='1) rejected_o<=1;
                    else begin
                      requested_enable_q<=0;recovery_boot_q<=boot_epoch_o;state_q<=SETTLE;
                      reset_low_q<=0;reset_confirmed_q<=0;recover_sent_q<=0;
                    end
                  end
                  default: rejected_o<=1;
                endcase
              end else if(command_i.register_index==10'(INDEX_TRAIN_ACK) && command_i.command_mask==1) begin
                if(!train_request_o || command_i.training_token!=boot_epoch_o || fault_o) rejected_o<=1;
                else begin train_ack_o<='1;for(int c=0;c<CHANNELS;c++) train_token_o[c]<=command_i.training_token;end
              end else if(command_i.register_index==10'(INDEX_PERF_SNAPSHOT) && command_i.command_mask==1) begin
                pending_q<=CHANNELS'(command_i.destinations);state_q<=SEND_PERF;
              end else rejected_o<=1;
            end
          end else if(requested_enable_q && all_run && host_settled_i && !wake_request_i
              && ((|power_down_due) || (|self_refresh_due)) && profile_valid_o && !fault_o && !publication_busy_i && !(|management_request_i)) begin
            operation_q<=(|power_down_due) ? CH_CMD_POWER_DOWN : CH_CMD_SELF_REFRESH;
            pending_q<=(|power_down_due) ? power_down_due : self_refresh_due;
            sleep_mask_q<=(|power_down_due) ? power_down_due : self_refresh_due;state_q<=SEND;
          end else if(requested_enable_q && all_runnable && (|quiet_mask) && profile_valid_o && trained_o && trusted_o
              && !(|management_request_i) && !publication_busy_i && !fault_o) begin
            operation_q<=CH_CMD_ENABLE;pending_q<=quiet_mask;state_q<=SEND;
          end
        end
        SEND,SEND_RECOVER,SEND_WAKE: begin
          for(int c=0;c<CHANNELS;c++) if(pending_q[c] && command_ready_i[c]) pending_q[c]<=0;
          if((pending_q & ~command_ready_i)==0) begin
            if(state_q==SEND_RECOVER) state_q<=WAIT_BOOT;
            else if(state_q==SEND_WAKE) state_q<=WAIT_WAKE;
            else if(operation_q inside {CH_CMD_POWER_DOWN,CH_CMD_SELF_REFRESH}) state_q<=WAIT_SLEEP;
            else if(operation_q==CH_CMD_ENABLE) state_q<=WAIT_ENABLE;
            else state_q<=WAIT_QUIESCE;
          end
        end
        SEND_PERF: begin
          pending_q<=pending_q & ~perf_ready_i;
          if((pending_q & ~perf_ready_i)==0) state_q<=IDLE;
        end
        WAIT_SLEEP: if(sleep_settled) state_q<=sleep_quiet ? IDLE : SLEEP;
        SLEEP: begin
          if(sleep_quiet) state_q<=IDLE;
          else if(wake_request_i || command_valid_i || publication_busy_i || (|management_request_i)) begin
            pending_q<=sleeping_mask;state_q<=SEND_WAKE;
          end
        end
        WAIT_WAKE: if(sleep_quiet) state_q<=IDLE;
        WAIT_ENABLE: if(all_run) begin requested_enable_q<=1;state_q<=IDLE;end
        WAIT_QUIESCE: if(all_quiet && host_settled_i) state_q<=IDLE;
        SETTLE: if(host_settled_i && all_failed) begin state_q<=RESET_WAIT;reset_low_q<=0;end
        RESET_WAIT: begin
          // A stale high acknowledgement from an earlier reset is insufficient.
          reset_low_q<=reset_low_q | ~destructive_reset_confirmed_i;
          if((&reset_low_q) && (&destructive_reset_confirmed_i) && !external_fault_i) begin
            reset_confirmed_q<=destructive_reset_confirmed_i;pending_q<='1;state_q<=SEND_RECOVER;
          end
        end
        WAIT_BOOT: if(all_boot && host_settled_i) begin host_recover_o<=1;recover_sent_q<=1;state_q<=RECOVER_HOST;end
        RECOVER_HOST: if(recover_sent_q && !host_fault_i) begin
          fault_q<=0;rearm_sent_q<=0;rearm_low_seen_q<=0;recover_sent_q<=0;
          installed_epoch_q<=0;installed_boot_q<=boot_epoch_o;
          recovery_complete_o<=1;state_q<=IDLE;
        end
        default: begin fault_q<=1;requested_enable_q<=0;state_q<=IDLE;end
      endcase
      if(normal_state && (external_fault_i || channel_fault || (control_fault_i && !control_fault_seen_q) || (host_monitored_q && host_fault_i))) begin
        fault_q<=1;requested_enable_q<=0;
        if(state_q!=IDLE) begin state_q<=IDLE;rejected_o<=1;end
      end
      if((state_q==SEND || state_q==WAIT_ENABLE || state_q==WAIT_QUIESCE) && (|command_rejected_i)) begin
        fault_q<=1;requested_enable_q<=0;rejected_o<=1;state_q<=IDLE;
      end
      if(!(state_q inside {IDLE,SLEEP}) && timer_q>=32'(COMMAND_TIMEOUT-1)) begin
        fault_q<=1;requested_enable_q<=0;rejected_o<=1;state_q<=IDLE;
      end
      // Outside recovery, a control fault remains sticky until actual reset,
      // epoch advancement and host recovery complete. IRQ clear has no input.
      if(!recovering && !normal_state) fault_q<=1;
    end
  end
endmodule
