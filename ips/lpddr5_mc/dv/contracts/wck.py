"""DFI-offset experiment, deliberately separate from the pin-event oracle.

Time is integer half-WCK periods. Offsets are whole WCK periods, following
DFI 5.0 Table 39. PHY delays are reference-model choices, not PHY guarantees.
This model does not generate the individual differential WCK edges.
"""

from dataclasses import dataclass
from typing import Literal

WCK_FIELDS = frozenset(
    {
        "twck_dis",
        "twck_en_fs",
        "twck_en_rd",
        "twck_en_wr",
        "twck_fast_toggle",
        "twck_toggle",
        "twck_toggle_cs",
        "twck_toggle_post",
        "twck_toggle_rd",
        "twck_toggle_wr",
    }
)

INTEGER_FIELDS = frozenset(
    {
        "data_rate_mbps",
        "ck_mhz",
        "wck_to_ck",
        "ranks",
        "read_latency_ck",
        "write_latency_ck",
        "mr2_rl_code",
        "mr3_bank_org",
        "mr18_ckr",
        "mr19_dvfsc",
        "mr22_recc",
        "static_preamble_ck",
        "read_enable_ck",
        "write_enable_ck",
        "fast_enable_ck",
        "read_toggle_ck",
        "write_toggle_ck",
        "postamble_half_wck",
        "phy_command_delay_half_wck",
        "phy_wck_delay_half_wck",
    }
)
PROFILE_FIELDS = INTEGER_FIELDS | {"name", "bank_mode", "link_ecc", "offsets_wck", "integration_qualified"}


@dataclass(frozen=True)
class Events:
    cas: int
    command: int
    enable: int
    slow_toggle: int
    fast_toggle: int
    first_data: int
    data_end: int
    stop: int
    disable: int


def validate_profile(profile: dict) -> None:
    """Validate this experiment's representation, without admitting traffic."""
    if set(profile) != PROFILE_FIELDS:
        raise ValueError("missing or unknown profile field")
    if any(type(profile[k]) is not int or not 0 <= profile[k] <= 65535 for k in INTEGER_FIELDS):
        raise ValueError("profile integer not representable in unsigned 16 bits")
    if any(type(profile[k]) is not bool for k in ("link_ecc", "integration_qualified")):
        raise ValueError("invalid boolean field")
    if type(profile["name"]) is not str or not profile["name"]:
        raise ValueError("profile identity required")
    if profile["integration_qualified"]:
        raise ValueError("this experiment cannot establish integration qualification")
    ratio = profile["wck_to_ck"]
    if type(ratio) is not int or ratio not in (2, 4):
        raise ValueError("unsupported WCK:CK ratio")
    if profile["data_rate_mbps"] != 2 * ratio * profile["ck_mhz"]:
        raise ValueError("inconsistent frequencies")
    if (profile["bank_mode"], ratio) not in (("BG", 4), ("16B", 2)):
        raise ValueError("unsupported bank/ratio experiment")
    if profile["ranks"] != 1 or profile["mr19_dvfsc"] != 0:
        raise ValueError("multi-rank and DVFSC not qualified")
    if profile["link_ecc"] and ratio != 4:
        raise ValueError("no 2:1 read-link-ECC reference row")
    if profile["ck_mhz"] != 800 or profile["mr2_rl_code"] != (11 if ratio == 4 else 5):
        raise ValueError("unsupported speed/MR2 experiment")
    if (profile["mr3_bank_org"], profile["mr18_ckr"]) != ((0, 0) if ratio == 4 else (2, 1)):
        raise ValueError("mode-register fields disagree with bank/clock mode")
    if profile["mr22_recc"] != int(profile["link_ecc"]):
        raise ValueError("read link mode disagrees with MR22 field")
    offsets = profile["offsets_wck"]
    if type(offsets) is not dict or set(offsets) != WCK_FIELDS:
        raise ValueError("missing or unknown WCK offset")
    if any(type(v) is not int or not 0 <= v <= 65535 for v in offsets.values()):
        raise ValueError("offset not representable in unsigned 16 bits")
    for key in ("phy_command_delay_half_wck", "phy_wck_delay_half_wck"):
        if type(profile[key]) is not int or not 0 <= profile[key] <= 65535:
            raise ValueError("invalid PHY path delay")
    if offsets["twck_fast_toggle"] != ratio:
        raise ValueError("reference requires one CK of half-rate toggle")
    fast_at_pin = 2 * offsets["twck_en_fs"] + profile["phy_wck_delay_half_wck"] - profile["phy_command_delay_half_wck"]
    if fast_at_pin != profile["fast_enable_ck"] * 2 * ratio:
        raise ValueError("fast-enable metadata disagrees with offset/path delays")
    if any(offsets[k] != 0 for k in ("twck_toggle_cs", "twck_toggle_rd", "twck_toggle_wr")):
        raise ValueError("rank switches/static restart are outside the experiment")
    for direction in ("read", "write"):
        ev = derive_events(profile, direction, 32)
        ck = 2 * ratio
        if ev.enable - ev.cas != profile[direction + "_enable_ck"] * ck:
            raise ValueError("enable metadata disagrees with offset/path delays")
        if ev.slow_toggle - ev.enable != profile["static_preamble_ck"] * ck:
            raise ValueError("static preamble metadata disagrees with offset")
        if ev.first_data - ev.slow_toggle != profile[direction + "_toggle_ck"] * ck:
            raise ValueError("toggle metadata disagrees with latency/offset")
        if 2 * offsets["twck_toggle_post"] < profile["postamble_half_wck"]:
            raise ValueError("represented postamble below the selected minimum")
        if not ev.cas <= ev.enable < ev.slow_toggle < ev.fast_toggle <= ev.first_data:
            raise ValueError("inconsistent event ordering")
        if not ev.data_end < ev.stop <= ev.disable:
            raise ValueError("postamble/disable precedes data completion")


def derive_events(profile: dict, direction: Literal["read", "write"], beats: int) -> Events:
    if direction not in ("read", "write") or type(beats) is not int or beats not in (16, 32):
        raise ValueError("unsupported burst")
    ratio = profile["wck_to_ck"]
    ck = 2 * ratio
    offsets = profile["offsets_wck"]
    cas = profile["phy_command_delay_half_wck"]
    command = cas + ck
    suffix = "rd" if direction == "read" else "wr"
    enable = 2 * offsets["twck_en_" + suffix] + profile["phy_wck_delay_half_wck"]
    slow = enable + 2 * offsets["twck_toggle"]
    fast = slow + 2 * offsets["twck_fast_toggle"]
    first = command + profile[direction + "_latency_ck"] * ck
    # BL32 in BG4 has two 16-beat halves separated by eight WCK.
    span = beats + (16 if profile["bank_mode"] == "BG" and beats == 32 else 0)
    end = first + span
    stop = end + 2 * offsets["twck_toggle_post"]
    disable = command - cas + 2 * offsets["twck_dis"] + profile["phy_wck_delay_half_wck"]
    return Events(cas, command, enable, slow, fast, first, end, stop, disable)


def require_integration_qualification(profile: dict) -> None:
    """No experiment in this delivery authorizes ordinary controller traffic."""
    validate_profile(profile)
    raise ValueError("full pin waveform and integration qualification remain pending")


def edge_train(event: Events) -> tuple[int, ...]:
    """Half-rate then full-rate transitions, with a trained zero-phase fixture.

    The first transition at slow_toggle is rising. This is a reference-model
    phase assumption; it is not an electrical timing or trained-PHY result.
    """
    return tuple(range(event.slow_toggle, event.fast_toggle, 2)) + tuple(range(event.fast_toggle, event.stop))
