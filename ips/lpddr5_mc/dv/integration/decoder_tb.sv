module decoder_tb;
  import lpddr5_mc_apb_metadata_pkg::*;
  import lpddr5_mc_channel_pkg::*;
  logic clk=0;
  always #0.5 clk<=~clk;
  lpddr5_mc_register_image_t image,image_q;
  lpddr5_mc_profile_t [2:0] qualified,qualified_q;
  lpddr5_mc_profile_t profile;
  lpddr5_mc_channel_config_t config_value;
  logic valid_value;
  logic [1:0] fsp;
  logic [4:0] frequency;
  logic [31:0] boot_epoch=32'hfedcba98,config_epoch=32'h12345678;
  int checks=0;
  logic unused_config;
  assign unused_config=^config_value;
  // Model the real frozen mailbox source, including its sampling edge.
  always_ff @(posedge clk) begin image_q<=image;qualified_q<=qualified;end
  lpddr5_mc_channel_register_decode dut (
    .image_i(image_q),.qualified_profiles_i(qualified_q),.boot_epoch_i(boot_epoch),.config_epoch_i(config_epoch),
    .config_o(config_value),.valid_o(valid_value),.target_fsp_o(fsp),.target_frequency_o(frequency));
  `include "expected_profiles.svh"
  task automatic check(input logic condition,input string message);
    assert(condition) else $fatal(1,"%s",message);checks++;
  endtask
  initial begin
    string path;
    int stream,count,number,ranks,target;
    logic [31:0] prior_value;
    check($value$plusargs("images=%s",path),"missing image vectors");stream=$fopen(path,"r");
    check(stream!=0 && $fscanf(stream,"%d",count)==1 && count==192,"image vector header");
    for(int c=0;c<count;c++) begin
      check($fscanf(stream,"%d %d %d",number,ranks,target)==3,"image case");
      for(int r=0;r<REG_COUNT;r++) check($fscanf(stream,"%h",image[r])==1,"image word");
      expected_profile(number);profile.ranks=2'(ranks);
      for(int f=0;f<3;f++) qualified[f]=profile;
      #1;
      check(valid_value && config_value.profile==profile,$sformatf("software/PHY profile mismatch %0d ranks%0d fsp%0d",number,ranks,target));
      check(fsp==2'(target) && frequency==(profile.ratio==2 ? 0 : 1),"FSP/frequency decode");
      check(config_value.boot_epoch==boot_epoch && config_value.config_epoch==config_epoch,"coherent epoch decode");
      check(config_value.service_div==2 && config_value.age_tick_div==8 && config_value.read_urgent_age==32 && config_value.write_urgent_age==image[INDEX_SCHED_WR_AGE][15:0]
        && config_value.bw_window==3000 && config_value.read_cap==24000 && config_value.write_cap==24000,"policy decode");
      check(config_value.write_high==image[INDEX_WR_HIGH][6:0] && config_value.write_low==image[INDEX_WR_LOW][6:0]
        && config_value.read_batch_min==image[INDEX_RD_BATCH_MIN][7:0]
        && config_value.write_batch_min==image[INDEX_WR_BATCH_MIN][7:0] && config_value.write_batch_max==image[INDEX_WR_BATCH_MAX][7:0]
        && config_value.hit_cap==image[INDEX_HIT_CAP][15:0] && config_value.page_idle_ck==image[INDEX_PAGE_IDLE],"batch/page policy decode");
      case(target)
        0:image[INDEX_FSP0_TRTW]=257;
        1:image[INDEX_FSP1_TRTW]=257;
        2:image[INDEX_FSP2_TRTW]=257;
      endcase
      #1;check(valid_value && config_value.read_to_write_min_ck==257,"read-to-write minimum projection");
      case(target)
        0:image[INDEX_FSP0_TRTW]=0;
        1:image[INDEX_FSP1_TRTW]=0;
        2:image[INDEX_FSP2_TRTW]=0;
      endcase
      #1;
      // Mutate each complete narrowed scalar rather than only one example.
      // The full-width and derived timing CSRs are excluded from this test.
      for(int r=0;r<REG_COUNT;r++) begin
        if(REG_FSP[r]==target && !(r inside {
            INDEX_FSP0_TRAS_MAX,INDEX_FSP1_TRAS_MAX,INDEX_FSP2_TRAS_MAX,
            INDEX_FSP0_TREFI,INDEX_FSP1_TREFI,INDEX_FSP2_TREFI,
            INDEX_FSP0_RETENTION_BUDGET,INDEX_FSP1_RETENTION_BUDGET,INDEX_FSP2_RETENTION_BUDGET})) begin
          prior_value=image[r];image[r]|=REG_RW[r]==32'hff ? 32'h100 : 32'h10000;#1;
          check(!valid_value,$sformatf("overflowing scalar CSR index%0d admitted",r));image[r]=prior_value;#1;
          check(valid_value,"restored scalar image did not validate");
        end
      end
      prior_value=image[INDEX_AGE_TICK_DIV];image[INDEX_AGE_TICK_DIV]|=32'h10000;#1;
      check(!valid_value,"age divisor silently truncated");image[INDEX_AGE_TICK_DIV]=prior_value;
      prior_value=image[INDEX_SCHED_RD_AGE];image[INDEX_SCHED_RD_AGE]|=32'h10000;#1;
      check(!valid_value,"urgent age silently truncated");image[INDEX_SCHED_RD_AGE]=prior_value;
      prior_value=image[INDEX_RD_WEIGHT];image[INDEX_RD_WEIGHT]|=32'h10000;#1;
      check(!valid_value,"read weight silently truncated");image[INDEX_RD_WEIGHT]=prior_value;
      prior_value=image[INDEX_WR_WEIGHT];image[INDEX_WR_WEIGHT]|=32'h10000;#1;
      check(!valid_value,"write weight silently truncated");image[INDEX_WR_WEIGHT]=prior_value;#1;
      prior_value=image[INDEX_SCHED_WR_AGE];image[INDEX_SCHED_WR_AGE]=32'hffff;#1;
      check(valid_value && config_value.write_urgent_age==16'hffff && config_value.read_urgent_age==32,"distinct direction age thresholds");
      image[INDEX_SCHED_WR_AGE]=prior_value;
      prior_value=image[INDEX_RD_WEIGHT];image[INDEX_RD_WEIGHT]=32'hfffe;#1;
      check(valid_value && config_value.read_weight==16'hfffe,"full-width read weight");image[INDEX_RD_WEIGHT]=prior_value;
      prior_value=image[INDEX_WR_WEIGHT];image[INDEX_WR_WEIGHT]=32'hffff;#1;
      check(valid_value && config_value.write_weight==16'hffff,"full-width write weight");image[INDEX_WR_WEIGHT]=prior_value;#1;
      image[INDEX_FSP_VALID]=0;#1;check(!valid_value,"invalid target bank admitted");
      image[INDEX_FSP_VALID]=32'd1<<target;
      image[INDEX_DVFS]=3;#1;check(!valid_value,"reserved target bank admitted");image[INDEX_DVFS]=32'(target);
      case(target)
        0:image[INDEX_FSP0_TZQCAL]|=32'h10000;
        1:image[INDEX_FSP1_TZQCAL]|=32'h10000;
        2:image[INDEX_FSP2_TZQCAL]|=32'h10000;
      endcase
      #1;check(!valid_value,"software 32-bit field silently truncated");
    end
    $fclose(stream);$display("DECODER_INTEGRATION_PASS checks=%0d cases=%0d profiles=32 ranks=1/2 fsp=0/1/2",checks,count);$finish;
  end
endmodule
