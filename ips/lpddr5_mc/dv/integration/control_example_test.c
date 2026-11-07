/* Behavior tests compile the delivered example itself, with ordered MMIO. */
#include "../../sw/examples/lpddr5_mc_control.c"
#include <assert.h>
#include <stdio.h>

struct device {
    uint32_t status, epoch, boot, request, token;
    unsigned writes, polls, complete_after, fail_after, reset_after;
    int publish_valid, publish_epoch;
};

static uint32_t read_device(void *context, uint32_t offset)
{
    struct device *d = context;
    if (offset == LPDDR5_MC_STATUS) {
        if (d->writes) {
            ++d->polls;
            if (d->fail_after && d->polls >= d->fail_after)
                d->status |= LPDDR5_MC_STATUS_FAULT_MASK;
            if (d->reset_after && d->polls >= d->reset_after)
                d->boot = 8;
            if (d->complete_after && d->polls >= d->complete_after) {
                if (d->publish_valid)
                    d->status |= LPDDR5_MC_STATUS_PROFILE_VALID_MASK;
                if (d->publish_epoch)
                    d->epoch = 19;
            }
        }
        return d->status;
    }
    if (offset == LPDDR5_MC_CONFIG_EPOCH) return d->epoch;
    if (offset == LPDDR5_MC_BOOT_EPOCH) return d->boot;
    if (offset == LPDDR5_MC_TRAIN_REQUEST) return d->request;
    return 0;
}

static void write_device(void *context, uint32_t offset, uint32_t value)
{
    struct device *d = context;
    if (offset == LPDDR5_MC_CONFIG_COMMIT) {
        assert(value == LPDDR5_MC_CONFIG_COMMIT_GO_MASK);
        ++d->writes;
    } else if (offset == LPDDR5_MC_TRAIN_TOKEN) d->token = value;
    else {
        assert(offset == LPDDR5_MC_TRAIN_ACK && value == LPDDR5_MC_TRAIN_ACK_GO_MASK);
        assert(d->token == d->boot);
        ++d->writes;
    }
}

static struct device initial(uint32_t status)
{
    struct device d = {0};
    d.status = status; d.epoch = 18; d.boot = 7;
    d.complete_after = 3; d.publish_valid = 1; d.publish_epoch = 1;
    return d;
}

int main(void)
{
    struct device d = initial(0);
    struct lpddr5_mc_mmio io = {&d, read_device, write_device};
    assert(lpddr5_mc_publish_configuration(&io, 3) == 0 && d.writes == 1 && d.polls == 3);
    puts("CONTROL_EXAMPLE cold disabled publication passes at final allowed poll");
    d = initial(LPDDR5_MC_STATUS_QUIESCED_MASK | LPDDR5_MC_STATUS_PROFILE_VALID_MASK | (1u << 8));
    assert(lpddr5_mc_publish_configuration(&io, 8) == 0 && d.writes == 1);
    puts("CONTROL_EXAMPLE quiesced publication requires a new epoch");
    for (unsigned state = 2; state <= 5; ++state) {
        d = initial(state << 8);
        assert(lpddr5_mc_publish_configuration(&io, 8) == -1 && d.writes == 0);
    }
    d = initial(LPDDR5_MC_STATUS_FAULT_MASK | LPDDR5_MC_STATUS_QUIESCED_MASK);
    assert(lpddr5_mc_publish_configuration(&io, 8) == -1 && d.writes == 0);
    puts("CONTROL_EXAMPLE running/transfer/sleep/fault states reject before commit");
    d = initial(0); d.fail_after = 2;
    assert(lpddr5_mc_publish_configuration(&io, 8) == -1 && d.polls == 2 && d.writes == 1);
    d = initial(0); d.reset_after = 2;
    assert(lpddr5_mc_publish_configuration(&io, 8) == -1 && d.polls == 2);
    puts("CONTROL_EXAMPLE publication fault or changed boot rejects without retry");
    for (unsigned mode = 0; mode < 3; ++mode) {
        d = initial(0);
        if (mode == 0) d.complete_after = 0;
        if (mode == 1) d.publish_valid = 0;
        if (mode == 2) d.publish_epoch = 0;
        assert(lpddr5_mc_publish_configuration(&io, 4) == -2 && d.polls == 4 && d.writes == 1);
    }
    puts("CONTROL_EXAMPLE no completion/invalid image/unchanged epoch times out with one commit");
    d = initial(0);
    assert(lpddr5_mc_publish_configuration(&io, 0) == -1 && d.writes == 0);
    assert(lpddr5_mc_publish_configuration(NULL, 8) == -1);
    struct lpddr5_mc_mmio invalid = {&d, NULL, write_device};
    assert(lpddr5_mc_publish_configuration(&invalid, 8) == -1);
    invalid.read32 = read_device; invalid.write32 = NULL;
    assert(lpddr5_mc_publish_configuration(&invalid, 8) == -1);
    assert(lpddr5_mc_acknowledge_training(&io) == -1 && d.writes == 0);
    d.request = 1; d.boot = 0;
    assert(lpddr5_mc_acknowledge_training(&io) == 0 && d.token == 0 && d.writes == 1);
    puts("CONTROL_EXAMPLE_PASS");
    return 0;
}
