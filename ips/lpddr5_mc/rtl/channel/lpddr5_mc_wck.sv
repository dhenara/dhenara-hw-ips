// One WCK owner. Rank/direction changes require full stop before a fresh sync.
module lpddr5_mc_wck (
  input logic clk_ck_i, rst_ni, abort_i,
  input lpddr5_mc_channel_pkg::lpddr5_mc_profile_t profile_i,
  input logic sync_i, sync_write_i, sync_rank_i,
  input logic access_i, access_write_i, access_bl32_i,
  input logic stop_i,
  output logic disabled_o, reusable_o, fault_o, stop_ready_o,
  output logic active_rank_o, active_write_o,
  output lpddr5_mc_contract_pkg::lpddr5_mc_wck4_t wck_o
);
  import lpddr5_mc_channel_pkg::*;
  logic [63:0] tick_q, enable_q, slow_q, fast_q, post_q, disable_q, expiry_q;
  logic running_q, stopping_q;
  logic [63:0] at_tick, pin_end, stop_at;
  logic unused_profile;
  assign unused_profile=^profile_i;
  always_comb begin
    wck_o='0; at_tick=0;
    disabled_o=!running_q;
    // Serialized selection has already ended at disable_q, although
    // running_q retires on this edge. Never issue WS_OFF for that expired
    // epoch or extend its timer and resurrect the old rank on the next CK.
    stop_ready_o=running_q && !stopping_q && tick_q<disable_q && tick_q>=post_q && !access_i;
    reusable_o=running_q && !stopping_q && (profile_i.always_on || tick_q<expiry_q);
    for (int p=0; p<4; p++) begin
      at_tick=tick_q+64'(p);
      for (int b=0; b<2; b++) begin
        if (p<int'(profile_i.ratio) && running_q && at_tick<disable_q) begin
          wck_o.cs[p][b]=2'b1<<active_rank_o;
          wck_o.enable[p][b]=at_tick>=enable_q;
          wck_o.toggle[p][b]=at_tick<slow_q || (!profile_i.always_on && at_tick>=post_q) ? 2'b00 : at_tick<fast_q ? 2'b10 : 2'b11;
        end
      end
    end
    pin_end=tick_q*2+64'(profile_i.phy_half_wck_command)
      +(access_write_i ? 64'(profile_i.wl_ck) : 64'(profile_i.rl_ck))*64'(2*int'(profile_i.ratio))
      +(access_write_i ? 64'(profile_i.phy_half_wck_dq_input) : 64'(profile_i.phy_half_wck_dq_output))
      +(access_bl32_i ? (profile_i.ratio==4 ? 64'd48 : 64'd32) : 64'd16);
    // WCK path delay is removed from pin time when selecting DFI stop.
    stop_at=(pin_end+2*64'(profile_i.wck_twck_toggle_post)-64'(profile_i.phy_half_wck_wck)+1)/2;
  end
  always_ff @(posedge clk_ck_i or negedge rst_ni) begin
    if (!rst_ni) begin
      tick_q<=0; enable_q<=0; slow_q<=0; fast_q<=0; post_q<=0;
      disable_q<=0; expiry_q<=0; running_q<=0; stopping_q<=0;
      active_rank_o<=0; active_write_o<=0; fault_o<=0;
    end else if (abort_i) begin
      tick_q<=0; enable_q<=0; slow_q<=0; fast_q<=0; post_q<=0;
      disable_q<=0; expiry_q<=0; running_q<=0; stopping_q<=0;
      active_rank_o<=0; active_write_o<=0; fault_o<=0;
    end else begin
      tick_q<=tick_q+64'(profile_i.ratio);
      if (running_q && tick_q>=disable_q) begin running_q<=0; stopping_q<=0; end
      if (sync_i) begin
        if (running_q) fault_o<=1;
        else begin
          running_q<=1; stopping_q<=0; active_rank_o<=sync_rank_i; active_write_o<=sync_write_i;
          enable_q<=tick_q+(sync_write_i ? 64'(profile_i.wck_twck_en_wr) : 64'(profile_i.wck_twck_en_rd));
          slow_q<=tick_q+(sync_write_i ? 64'(profile_i.wck_twck_en_wr) : 64'(profile_i.wck_twck_en_rd))+64'(profile_i.wck_twck_toggle);
          fast_q<=tick_q+(sync_write_i ? 64'(profile_i.wck_twck_en_wr) : 64'(profile_i.wck_twck_en_rd))
            +64'(profile_i.wck_twck_toggle)+64'(profile_i.wck_twck_fast_toggle);
          disable_q<='1; expiry_q<='1; post_q<=0;
        end
      end
      if (access_i) begin
        if (!running_q || access_write_i!=active_write_o || stopping_q) fault_o<=1;
        if (stop_at>post_q) post_q<=stop_at;
        expiry_q<=tick_q+64'(int'(profile_i.ratio)*(access_bl32_i ? 8 : 4));
        if (!profile_i.always_on) disable_q<=tick_q+64'(profile_i.wck_twck_dis);
      end
      if (stop_i) begin
        if (!running_q || stopping_q || tick_q>=disable_q || tick_q<post_q || access_i) fault_o<=1;
        else begin stopping_q<=1; disable_q<=tick_q+64'(profile_i.wck_twck_dis); end
      end
    end
  end
endmodule
