// Link rows: JESD209-5C Tables 331/332. Bit zero is beat0/DQ0.
// Inline layout is controller SECDED64+8, Hamming 1..71, overall parity72.
package lpddr5_mc_ecc_pkg;
  typedef struct packed {
    logic [63:0] data;
    logic corrected, uncorrectable;
    logic [6:0] syndrome;
  } inline_result_t;
  typedef struct packed {
    logic [127:0] data;
    logic corrected, uncorrectable;
    logic [8:0] syndrome;
  } link_result_t;
  typedef struct packed {
    logic [15:0] data;
    logic corrected, uncorrectable;
    logic [5:0] syndrome;
  } dmi_result_t;
  localparam logic [8:0][136:0] LINK_ROWS = {
    137'b11111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111111,
    137'b01000000011111111000000001111111100000000111111110000000011111111000000001111111100000000111111110000000011111111000000001111111100000000,
    137'b00100000000000000111111110000000011111111000000001111111100000000111111111111111100000000111111110000000011111111000000001111111100000000,
    137'b00010000000000000111111111111111100000000111111110000000011111111000000000000000011111111000000001111111100000000111111111111111100000000,
    137'b00001000011111111000000000000000011111111000000001111111111111111000000000000000011111111000000001111111111111111000000000000000011111111,
    137'b00000100011111111000000000000000011111111111111110000000000000000111111110000000011111111111111110000000000000000111111110000000011111111,
    137'b00000010011110000111100001111000011110000111100001111000011110000111100001111000011110000111100001111000011110000111100001111000011110000,
    137'b00000001011001100110011001100110011001100110011001100110011001100110011001100110011001100110011001100110011001100110011001100110011001100,
    137'b00000000110101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010
  };
  localparam logic [5:0][21:0] DMI_ROWS = {
    22'b1111111111111111111111,
    22'b0100001111111111110000,
    22'b0010001111000011111111,
    22'b0001001111111100001111,
    22'b0000101100110011001100,
    22'b0000011010101010101010
  };

  function automatic logic [7:0] inline_encode(input logic [63:0] data);
    logic [71:1] code;
    logic [7:0] checks;
    int bit_index;
    code='0; bit_index=0;
    for (int pos=1; pos<=71; pos++) begin
      if ((pos & (pos-1)) != 0) begin
        code[pos]=data[bit_index]; bit_index++;
      end
    end
    checks='0;
    for (int p=0; p<7; p++) begin
      for (int pos=1; pos<=71; pos++)
        if ((pos & (1<<p)) != 0) checks[p] ^= code[pos];
      code[1<<p]=checks[p];
    end
    checks[7]=^code;
    return checks;
  endfunction

  function automatic inline_result_t inline_decode(input logic [63:0] data, input logic [7:0] checks);
    inline_result_t result;
    logic [71:1] code;
    logic odd;
    int bit_index;
    code='0; bit_index=0; result='0;
    for (int pos=1; pos<=71; pos++) begin
      if ((pos & (pos-1)) != 0) begin
        code[pos]=data[bit_index]; bit_index++;
      end
    end
    for (int p=0; p<7; p++) code[1<<p]=checks[p];
    odd=(^code)^checks[7];
    for (int p=0; p<7; p++)
      for (int pos=1; pos<=71; pos++)
        if ((pos & (1<<p)) != 0) result.syndrome[p] ^= code[pos];
    result.uncorrectable = result.syndrome != 0 && (!odd || result.syndrome > 71);
    result.corrected = odd && !result.uncorrectable;
    if (result.corrected && result.syndrome != 0) code[result.syndrome]=~code[result.syndrome];
    bit_index=0;
    for (int pos=1; pos<=71; pos++)
      if ((pos & (pos-1)) != 0) begin
        result.data[bit_index]=code[pos]; bit_index++;
      end
    if (result.uncorrectable) result.data='0;
    return result;
  endfunction

  function automatic logic [8:0] link_encode(input logic [127:0] data);
    logic [136:0] code;
    code={9'b0,data};
    for (int p=0; p<8; p++) code[128+p]=^(code & LINK_ROWS[p]);
    code[136]=^(code & LINK_ROWS[8]);
    return code[136:128];
  endfunction

  function automatic link_result_t link_decode(input logic [127:0] data, input logic [8:0] checks);
    logic [136:0] code;
    logic [8:0] column;
    logic matched;
    link_result_t result;
    code={checks,data}; result='0; matched=0;
    for (int p=0; p<9; p++) result.syndrome[p]=^(code & LINK_ROWS[p]);
    for (int b=0; b<137; b++) begin
      for (int p=0; p<9; p++) column[p]=LINK_ROWS[p][b];
      if (result.syndrome[8] && column==result.syndrome) begin
        code[b]=~code[b]; matched=1;
      end
    end
    result.corrected=matched;
    result.uncorrectable=result.syndrome != 0 && !matched;
    result.data=result.uncorrectable ? '0 : code[127:0];
    return result;
  endfunction

  function automatic logic [5:0] dmi_encode(input logic [15:0] data);
    logic [21:0] code;
    code={6'b0,data};
    for (int p=0; p<5; p++) code[16+p]=^(code & DMI_ROWS[p]);
    code[21]=^(code & DMI_ROWS[5]);
    return code[21:16];
  endfunction

  function automatic dmi_result_t dmi_decode(input logic [15:0] data, input logic [5:0] checks);
    logic [21:0] code;
    logic [5:0] column;
    logic matched;
    dmi_result_t result;
    code={checks,data}; result='0; matched=0;
    for (int p=0; p<6; p++) result.syndrome[p]=^(code & DMI_ROWS[p]);
    for (int b=0; b<22; b++) begin
      for (int p=0; p<6; p++) column[p]=DMI_ROWS[p][b];
      if (result.syndrome[5] && column==result.syndrome) begin code[b]=~code[b]; matched=1; end
    end
    result.corrected=matched;
    result.uncorrectable=result.syndrome != 0 && !matched;
    result.data=result.uncorrectable ? '0 : code[15:0];
    return result;
  endfunction
endpackage
