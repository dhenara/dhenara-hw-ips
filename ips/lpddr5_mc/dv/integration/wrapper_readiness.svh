// Fixture-only phase selection, shared with the timed seam regression.
// Records carry the actual raw snapshot identity; eligibility never invents
// an observation or substitutes a current falling value for a rising value.
typedef struct packed {
  bit valid,eligible;
  int tick;
  longint unsigned ps;
  logic [31:0] boot,config_epoch;
} wm_phase_point_t;
wm_phase_point_t wm_phase_rising[CHANNELS],wm_phase_falling[CHANNELS];
task automatic wm_phase_invalidate(input int channel);
  assert(channel>=0 && channel<CHANNELS) else $fatal(1,"invalid readiness channel");
  wm_phase_rising[channel].valid=0;wm_phase_falling[channel].valid=0;
endtask
task automatic wm_phase_record(input int channel,input bit rising,input int tick,
    input longint unsigned ps,input bit reset_ok,input logic [31:0] boot,config_epoch,input bit eligible);
  wm_phase_point_t point;
  assert(channel>=0 && channel<CHANNELS) else $fatal(1,"invalid readiness channel");
  point='{reset_ok,eligible,tick,ps,boot,config_epoch};
  // Passive pre-NBA observations must be available immediately to other
  // fixture callbacks; these are testbench records, not clocked RTL state.
  /* verilator lint_off BLKSEQ */
  if(rising) wm_phase_rising[channel]=point;
  else wm_phase_falling[channel]=point;
  /* verilator lint_on BLKSEQ */
endtask
function automatic bit wm_phase_aligned();
  for(int c=1;c<CHANNELS;c++)
    if(wm_phase_rising[c].tick!=wm_phase_rising[0].tick
        || wm_phase_rising[c].ps!=wm_phase_rising[0].ps
        || wm_phase_falling[c].tick!=wm_phase_falling[0].tick
        || wm_phase_falling[c].ps!=wm_phase_falling[0].ps) return 0;
  return 1;
endfunction
function automatic bit wm_phase_ready(input int channel,input int current_tick,
    input longint unsigned now_ps,input bit reset_ok,input logic [31:0] boot,config_epoch,input bit live_eligible);
  wm_phase_point_t rising_point,falling_point;
  if(channel<0 || channel>=CHANNELS) return 0;
  rising_point=wm_phase_rising[channel];falling_point=wm_phase_falling[channel];
  return live_eligible && reset_ok && rising_point.valid && falling_point.valid
    && rising_point.eligible && falling_point.eligible && wm_phase_aligned()
    && falling_point.tick==current_tick && rising_point.tick==falling_point.tick-1
    && rising_point.ps+625==falling_point.ps && now_ps==falling_point.ps+1
    && rising_point.boot==boot && falling_point.boot==boot
    && rising_point.config_epoch==config_epoch && falling_point.config_epoch==config_epoch;
endfunction
