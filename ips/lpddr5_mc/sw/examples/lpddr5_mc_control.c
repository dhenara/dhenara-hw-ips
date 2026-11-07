/* Integration example. Callbacks must perform ordered device MMIO accesses. */
#include "../generated/lpddr5_mc_registers.h"
#include <stddef.h>

typedef uint32_t (*lpddr5_mc_read32_fn)(void *context, uint32_t offset);
typedef void (*lpddr5_mc_write32_fn)(void *context, uint32_t offset, uint32_t value);

struct lpddr5_mc_mmio {
    void *context;
    lpddr5_mc_read32_fn read32;
    lpddr5_mc_write32_fn write32;
};

/* Returns 0 after queuing the matching acknowledgement, -1 if none is pending.
 * TRAIN_DONE is hardware state: software must poll it before enabling traffic.
 */
int lpddr5_mc_acknowledge_training(const struct lpddr5_mc_mmio *io)
{
    if (io == NULL || io->read32 == NULL || io->write32 == NULL)
        return -1;
    if ((io->read32(io->context, LPDDR5_MC_TRAIN_REQUEST) & 1u) == 0)
        return -1;
    uint32_t epoch = io->read32(io->context, LPDDR5_MC_BOOT_EPOCH);
    io->write32(io->context, LPDDR5_MC_TRAIN_TOKEN, epoch);
    io->write32(io->context, LPDDR5_MC_TRAIN_ACK, LPDDR5_MC_TRAIN_ACK_GO_MASK);
    return 0;
}

/* Stage a fully validated integration-specific image and settle the host first.
 * Call only in RESET (initial disabled publication) or after quiesce completes.
 * Hardware still checks real host settlement and all-channel atomic admission.
 * -1 is a precondition/fault, -2 is a bounded software wait expiration. Neither
 * timeout nor IRQ clearing authorizes recovery or proves retained data valid.
 */
int lpddr5_mc_publish_configuration(const struct lpddr5_mc_mmio *io, uint32_t poll_limit)
{
    if (io == NULL || io->read32 == NULL || io->write32 == NULL || poll_limit == 0)
        return -1;
    uint32_t status = io->read32(io->context, LPDDR5_MC_STATUS);
    if ((status & LPDDR5_MC_STATUS_FAULT_MASK) != 0 ||
        ((status & LPDDR5_MC_STATUS_QUIESCED_MASK) == 0 &&
         (status & LPDDR5_MC_STATUS_STATE_MASK) != 0))
        return -1;
    uint32_t prior_epoch = io->read32(io->context, LPDDR5_MC_CONFIG_EPOCH);
    uint32_t boot_epoch = io->read32(io->context, LPDDR5_MC_BOOT_EPOCH);
    io->write32(io->context, LPDDR5_MC_CONFIG_COMMIT, LPDDR5_MC_CONFIG_COMMIT_GO_MASK);
    for (uint32_t poll = 0; poll < poll_limit; ++poll) {
        status = io->read32(io->context, LPDDR5_MC_STATUS);
        if ((status & LPDDR5_MC_STATUS_FAULT_MASK) != 0)
            return -1;
        uint32_t current_epoch = io->read32(io->context, LPDDR5_MC_CONFIG_EPOCH);
        if (io->read32(io->context, LPDDR5_MC_BOOT_EPOCH) != boot_epoch)
            return -1;
        if ((status & LPDDR5_MC_STATUS_PROFILE_VALID_MASK) != 0 && current_epoch != prior_epoch)
            return 0;
    }
    return -2;
}
