# Channel timing integration

The implemented channel hierarchy uses `clk_ck_i` and asynchronous active-low reset `rst_ni`. The admitted digital fixture CK period is 1.25 ns. WCK ratios two/four are represented by phase payloads. Selection divisors one/two/four enable new picks inside the CK-rate owner; they do not create extra clocks. Host request/completion/configuration crossings belong to the host CDC owner.

No qualified top-level SDC, synthesis flow or physical timing result is supplied. The integrating owner must constrain the real CK input and synchronous PHY interface from the selected path profile, synchronize reset deassertion in each real domain, and check every clock/reset crossing. Reference digital path delays are integration inputs, not measured silicon margins. See [channel architecture and limits](../../docs/architecture/channel.md).
