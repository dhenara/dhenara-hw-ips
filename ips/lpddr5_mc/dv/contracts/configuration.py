"""Executable configuration and atomic APB publication contracts.

This is a reference model for later CSR/CDC RTL, not a bus implementation.
Publication is a two-phase operation: prepare all destinations, then commit all.
"""

from copy import deepcopy
from dataclasses import dataclass, field


def uint(value: object, width: int, name: str) -> int:
    if type(value) is not int or not 0 <= value < 1 << width:
        raise ValueError(name + " is not representable")
    return value


def validate_configuration(config: dict) -> None:
    expected = {
        "ports",
        "channels",
        "ranks",
        "axi_bits",
        "id_bits",
        "address_bits",
        "queue_depth",
        "outstanding",
        "service_div",
        "burst_bytes",
        "inline",
        "read_link",
        "write_link",
        "ratio",
        "bank_mode",
        "phy_raw_parity",
        "posted",
        "regions",
    }
    if set(config) != expected:
        raise ValueError("missing or unknown configuration field")
    for key in ("ports", "channels", "ranks"):
        if type(config[key]) is not int or config[key] not in (1, 2):
            raise ValueError(key)
    for key, allowed in [
        ("axi_bits", (64, 128, 256)),
        ("queue_depth", (16, 32, 64)),
        ("service_div", (1, 2, 4)),
        ("burst_bytes", (32, 64)),
        ("ratio", (2, 4)),
    ]:
        if type(config[key]) is not int or config[key] not in allowed:
            raise ValueError(key)
    if (
        not 4 <= uint(config["id_bits"], 5, "id_bits") <= 16
        or not 32 <= uint(config["address_bits"], 6, "address_bits") <= 40
    ):
        raise ValueError("AXI dimensions")
    if not 1 <= uint(config["outstanding"], 8, "outstanding") <= config["queue_depth"]:
        raise ValueError("credits exceed allocated queue")
    for key in ("inline", "read_link", "write_link", "phy_raw_parity", "posted"):
        if type(config[key]) is not bool:
            raise ValueError(key)
    if (config["ratio"], config["bank_mode"]) not in ((4, "BG"), (2, "16B")):
        raise ValueError("unsupported bank/ratio combination")
    if (config["read_link"] or config["write_link"]) and (config["ratio"] != 4 or not config["phy_raw_parity"]):
        raise ValueError("link ECC requires the 4:1 raw parity extension")
    regions = config["regions"]
    if type(regions) is not list or not 1 <= len(regions) <= 8:
        raise ValueError("region count")
    previous = 0
    total = 0
    for region in sorted(regions, key=lambda r: r["base"]):
        if set(region) != {"base", "size", "map_bits"}:
            raise ValueError("region fields")
        base = uint(region["base"], config["address_bits"], "region base")
        size = uint(region["size"], config["address_bits"] + 1, "region size")
        if (
            base % 4096
            or size < 4096
            or size & (size - 1)
            or base < previous
            or base + size > 1 << config["address_bits"]
        ):
            raise ValueError("region alignment, overlap or overflow")
        bits = region["map_bits"]
        # A permutation is invertible. XOR is permitted later only through a
        # separately proven invertible mapping; no lossy hash is guessed here.
        width = size.bit_length() - 1
        if (
            type(bits) is not list
            or bits[:6] != list(range(6))
            or any(type(x) is not int for x in bits)
            or sorted(bits) != list(range(width))
        ):
            raise ValueError("address map must be an invertible permutation")
        previous = base + size
        total += size
    per_rank = ((1 << 30) // 576) * 512 if config["inline"] else 1 << 30
    if total > per_rank * config["ranks"] * config["channels"]:
        raise ValueError("region capacity exceeds discovered devices/ECC reservation")


@dataclass
class RegisterBank:
    schema: dict
    channels: int
    enabled: bool = False
    quiesced: bool = False
    epoch: int = 0
    active_fsp: int = 0
    staging: list[dict[str, int]] = field(init=False)
    active: list[dict[str, int]] = field(init=False)
    pending: tuple[int, list[dict[str, int]]] | None = None
    valid: bool = False
    fault: bool = False
    sequence: int = 0
    mailbox: list[tuple[int, int, str, int, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.channels not in (1, 2):
            raise ValueError("channels")
        self.staging = [{r["name"]: r["reset"] for r in self.schema["registers"]} for _ in range(self.channels)]
        self.active = deepcopy(self.staging)

    def access(
        self,
        channel: int,
        name: str,
        write: bool = False,
        data: int = 0,
        strobes: int = 15,
        broadcast: bool = False,
        aligned: bool = True,
    ) -> int:
        """An APB access completes once; failure has no register side effect."""
        uint(data, 32, "APB data")
        uint(strobes, 4, "PSTRB")
        if not aligned or not 0 <= channel < self.channels or (not write and strobes):
            raise ValueError("PSLVERR: alignment/channel/read strobes")
        matches = [r for r in self.schema["registers"] if r["name"] == name]
        if not matches:
            raise ValueError("PSLVERR: unmapped")
        reg = matches[0]
        if not write:
            return self.staging[channel][name]
        targets = range(self.channels) if reg["scope"] == "shared" or broadcast and channel == 0 else [channel]
        if broadcast and (channel != 0 or reg["scope"] != "channel"):
            raise ValueError("PSLVERR: invalid broadcast")
        if self.pending:
            raise ValueError("PSLVERR: commit in flight")
        locked = self.enabled and (reg["lock"] == "disabled" or not self.quiesced and reg["lock"] != "none")
        if reg["lock"] == "inactive_or_quiesced" and not name.startswith(f"FSP{self.active_fsp}_"):
            locked = False
        if locked or all(f["access"] == "ro" for f in reg["fields"]):
            raise ValueError("PSLVERR: locked/read-only")
        byte_mask = sum(255 << (8 * i) for i in range(4) if strobes >> i & 1)
        commands = sum(
            bool(data & (((1 << f["width"]) - 1) << f["lsb"]) & byte_mask)
            for f in reg["fields"]
            if f["access"] == "w1s"
        ) * (1 if reg["scope"] == "shared" else len(targets))
        if self.sequence + commands >= 1 << 32:
            raise ValueError("mailbox sequence exhausted; drain/reset")
        for target in targets:
            value = self.staging[target][name]
            for f in reg["fields"]:
                mask = ((1 << f["width"]) - 1) << f["lsb"] & byte_mask
                if f["access"] == "rw":
                    value = (value & ~mask) | (data & mask)
                elif f["access"] == "w1c":
                    value &= ~(data & mask)
                elif f["access"] == "w1s" and data & mask and (reg["scope"] != "shared" or target == 0):
                    self.sequence += 1
                    self.mailbox.append((self.sequence, target, name, data & mask, data))
            self.staging[target][name] = value
        return 0

    def prepare(self, validator) -> int:
        if self.enabled and not self.quiesced or self.pending:
            raise ValueError("not quiesced or commit already pending")
        snapshot = deepcopy(self.staging)
        for state in snapshot:
            validator(state)
        sequence = self.sequence + 1
        if sequence >= 1 << 32:
            raise ValueError("configuration epoch exhausted")
        self.sequence = sequence
        self.pending = sequence, snapshot
        return sequence

    def acknowledge(self, sequence: int, ready_channels: set[int]) -> None:
        if not self.pending or sequence != self.pending[0] or ready_channels != set(range(self.channels)):
            raise ValueError("stale/partial CDC acknowledgement")
        sequence, snapshot = self.pending
        for target, state in enumerate(snapshot):
            for reg in self.schema["registers"]:
                if all(f["access"] in ("ro", "w1c") for f in reg["fields"]):
                    state[reg["name"]] = self.staging[target][reg["name"]]
        self.epoch, self.active = sequence, snapshot
        self.pending = None
        self.valid = True
        self.hardware_update(0, "CONFIG_EPOCH", self.epoch)

    def hardware_update(self, channel: int, name: str, value: int) -> None:
        """Atomic owner-to-APB status snapshot; software cannot write RO bits."""
        uint(value, 32, name)
        reg = next(r for r in self.schema["registers"] if r["name"] == name)
        if not 0 <= channel < self.channels or not all(f["access"] in ("ro", "w1c") for f in reg["fields"]):
            raise ValueError("status owner/access")
        mask = sum(((1 << f["width"]) - 1) << f["lsb"] for f in reg["fields"])
        if value & ~mask:
            raise ValueError("reserved status bits")
        for target in range(self.channels) if reg["scope"] == "shared" else [channel]:
            self.active[target][name] = value
            self.staging[target][name] = value

    def abort(self) -> None:
        self.pending = None
        self.valid = False
        self.enabled = False
        self.fault = True

    def reset(self) -> None:
        self.enabled = False
        self.quiesced = False
        self.pending = None
        self.sequence += 1
        self.epoch = self.sequence
        self.valid = False
        self.fault = False
        self.mailbox.clear()
        self.__post_init__()


def example_configuration() -> dict:
    import json
    from pathlib import Path

    return json.loads((Path(__file__).resolve().parents[2] / "config/contracts/example.json").read_text())


def map_address(address: int, region: dict) -> int:
    relative = address - region["base"]
    if not 0 <= relative < region["size"]:
        raise ValueError("address outside region")
    return sum((relative >> source & 1) << destination for destination, source in enumerate(region["map_bits"]))


def register_image(schema: dict, profile: dict, config: dict, fsp: int = 0) -> dict[str, int]:
    """Executable programming example; explicit integration descriptor required."""
    from contracts.profiles import admit

    admit(profile)
    validate_configuration(config)
    if fsp not in (0, 1, 2):
        raise ValueError("FSP bank")
    if (config["ratio"], config["bank_mode"], config["read_link"], config["write_link"]) != (
        profile["ratio"],
        profile["bank_mode"],
        profile["read_link"],
        profile["write_link"],
    ) or config["ranks"] > profile["ranks"]:
        raise ValueError("configuration disagrees with PHY profile")
    image = {r["name"]: r["reset"] for r in schema["registers"]}
    image.update(
        {
            "SCHED_RD_AGE": 32,
            "SCHED_WR_AGE": 32,
            "AGE_STEP": 1,
            "AGE_TICK_DIV": 8,
            "WR_HIGH": config["queue_depth"] * 3 // 4,
            "WR_LOW": config["queue_depth"] // 4,
            "RD_BATCH_MIN": 1,
            "WR_BATCH_MIN": 1,
            "WR_BATCH_MAX": 16,
            "HIT_CAP": 16,
            "PAGE_IDLE": 256,
            "BW_WINDOW": 3000,
            "BW_BURST": 64,
            "RD_CAP": 375 * 64,
            "WR_CAP": 375 * 64,
            "RD_WEIGHT": 1,
            "WR_WEIGHT": 1,
            "BW_MODE": 0,
            "TEMP_POLL": 3000,
            "ZQ_INTERVAL": 3000,
            "PORT_LIMIT_0": config["outstanding"] | (config["outstanding"] << 8),
            "PORT_LIMIT_1": config["outstanding"] | (config["outstanding"] << 8),
        }
    )
    image["MODE"] = (
        int(config["burst_bytes"] == 64)
        | (int(config["inline"]) << 1)
        | (int(config["read_link"]) << 2)
        | (int(config["write_link"]) << 3)
        | (int(config["posted"]) << 4)
        | (config["service_div"] << 8)
    )
    image["FSP_VALID"] = 1 << fsp
    for group in ("dram_ck", "wck", "dfi_wck", "management_ck"):
        for name, value in profile[group].items():
            key = f"FSP{fsp}_" + name.upper()
            # Integration descriptor contains some diagnostic bounds separately.
            if key in image:
                image[key] = value
    for key, value in profile["mr"].items():
        image[f"FSP{fsp}_MR{key}"] = value
    image[f"FSP{fsp}_RL"] = profile["rl_ck"]
    image[f"FSP{fsp}_WL"] = profile["wl_ck"]
    for i, region in enumerate(config["regions"]):
        image[f"REGION_{i}_BASE_LO"] = region["base"] & 0xFFFFFFFF
        image[f"REGION_{i}_BASE_HI"] = region["base"] >> 32
        image[f"REGION_{i}_SIZE_4K"] = region["size"] // 4096
        image[f"REGION_{i}_ATTR"] = (
            1 | (2 if config["channels"] == 2 else 0) << 1 | config["ranks"] << 3 | int(config["inline"]) << 5
        )
        image[f"REGION_{i}_MAP"] = 1
        for bit, source in enumerate(region["map_bits"]):
            image[f"REGION_{i}_BIT_{bit}"] = source
    return image


def validate_register_image(image: dict, profile: dict, config: dict, schema: dict, fsp: int = 0) -> None:
    from contracts.profiles import admit, waveform
    from contracts.waveform_oracle import check

    expected = register_image(schema, profile, config, fsp)
    if set(image) != set(expected):
        raise ValueError("register image shape")
    for reg in schema["registers"]:
        value = uint(image[reg["name"]], 32, reg["name"])
        mask = sum(((1 << f["width"]) - 1) << f["lsb"] for f in reg["fields"])
        if value & ~mask:
            raise ValueError("reserved register bits")
    # This integration fixture accepts exactly its reviewed region and option
    # descriptor, not a CSR image with a guessed replacement PHY capability.
    keys = [r["name"] for r in schema["registers"] if any(f["access"] == "rw" for f in r["fields"])]
    if any(image[k] != expected[k] for k in keys):
        raise ValueError("configuration/profile image mismatch")
    admit(profile)
    for direction in ("read", "write"):
        for beats in (16, 32):
            check(profile, waveform(profile, direction, beats))


def decode_address(config: dict, address: int) -> dict[str, int]:
    """Invertible region permutation, packed regions, then x16 geometry."""
    validate_configuration(config)
    uint(address, config["address_bits"], "address")
    prefix = 0
    for region in sorted(config["regions"], key=lambda r: r["base"]):
        if region["base"] <= address < region["base"] + region["size"]:
            logical = prefix + map_address(address, region)
            break
        prefix += region["size"]
    else:
        raise ValueError("DECERR: unmapped address")
    channel = (logical >> 6 & 1) if config["channels"] == 2 else 0
    local = ((logical & 63) | (logical >> 7 << 6)) if config["channels"] == 2 else logical
    per_rank = ((1 << 30) // 576) * 512 if config["inline"] else 1 << 30
    rank, local = divmod(local, per_rank)
    check = per_rank + local // 8 if config["inline"] else -1
    return {
        "channel": channel,
        "rank": rank,
        "bank": local >> 11 & 15,
        "row": local >> 15,
        "column_32b": (local & 2047) >> 5,
        "byte": local & 31,
        "inline_check_byte": check,
    }
