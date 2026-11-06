#include "lpddr5_mc_registers.h"
_Static_assert(LPDDR5_MC_CHANNEL_BASE == 0x10000, "channel base");
_Static_assert(LPDDR5_MC_CHANNEL_STRIDE == 0x10000, "channel stride");
_Static_assert(LPDDR5_MC_MODE_SERVICE_DIV_MASK == 0x700, "service field");
_Static_assert(LPDDR5_MC_ERROR_AXI_ID_VALUE_MASK == 0xffff, "full AXI ID");
_Static_assert(LPDDR5_MC_FSP0_RL == 0x1074, "timing bank");
int main(void) { return 0; }
