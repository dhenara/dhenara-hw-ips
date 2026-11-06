"""Admitted generic PHY fixtures; units and boundaries are explicit.

Profile admission validates representation and restricted capabilities. Independent
pin qualification lives in waveform_oracle.py; admission never trusts a flag in
input JSON. These fixtures do not qualify a vendor PHY or analog margins.
"""

from dataclasses import dataclass

from contracts.configuration import uint
from contracts.wck import WCK_FIELDS

TOP = {
    "name",
    "ck_ps",
    "ratio",
    "bank_mode",
    "ranks",
    "read_link",
    "write_link",
    "always_on",
    "read_dbi",
    "write_dbi",
    "data_mask",
    "device",
    "mr",
    "rl_ck",
    "wl_ck",
    "nwr_ck",
    "wck",
    "phy_half_wck",
    "dfi_wck",
    "management_ck",
    "dram_ck",
}
PHY = {"command", "wck", "write", "read", "dq_input", "dq_output", "trained_phase"}
DATA = {
    "tphy_wrlat",
    "tphy_wrdata",
    "twrdata_delay",
    "trddata_en",
    "tphy_rdlat",
    "tphy_wrcslat",
    "tphy_rdcslat",
    "tphy_wrcsgap",
    "tphy_rdcsgap",
}
MANAGEMENT = {
    "tcmd_lat",
    "tctrl_delay",
    "tctrlupd_min",
    "tctrlupd_max",
    "tphyupd_resp",
    "tphyupd_type0",
    "tphyupd_type1",
    "tphyupd_type2",
    "tphyupd_type3",
    "tinit_start",
    "tinit_complete",
    "tlp_resp",
    "tlp_ctrl_wakeup",
    "tlp_data_wakeup",
    "tphymstr_resp",
    "tphymstr_rfsh",
    "tphymstr_type0",
    "tphymstr_type1",
    "tphymstr_type2",
    "tphymstr_type3",
    "tinit_resp",
    "boot_timeout",
    "mr43_poll_bound",
    "mr43_epoch_limit",
    "tdram_clk_disable",
    "tdram_clk_enable",
    "retention_budget",
}
DRAM = {
    "trcd",
    "trp_pb",
    "trp_ab",
    "tras_min",
    "tras_max",
    "trc",
    "trrd",
    "tfaw",
    "twr",
    "trbtp",
    "twtr_s",
    "twtr_l",
    "tppd",
    "trfc_ab",
    "trfc_pb",
    "trfm_ab",
    "trfm_pb",
    "tpbr2pbr",
    "tpbr2act",
    "trefi",
    "txp",
    "tcke",
    "txsr",
    "tzqcal",
    "tzqlat",
    "tmrd",
    "tmrr",
    "tmrw",
    "tfc",
    "tckfspe",
    "tckfspx",
}
MRS = {1, 2, 3, 10, 11, 13, 16, 18, 19, 20, 21, 22, 23, 24, 25, 26, 28, 41, 46, 57}


def admit(p: dict) -> None:
    if set(p) != TOP or type(p["name"]) is not str or not p["name"]:
        raise ValueError("profile fields")
    for k, fields in [
        ("wck", WCK_FIELDS),
        ("phy_half_wck", PHY),
        ("dfi_wck", DATA),
        ("management_ck", MANAGEMENT),
        ("dram_ck", DRAM),
    ]:
        if type(p[k]) is not dict or set(p[k]) != fields:
            raise ValueError("missing/unknown " + k)
        for name, value in p[k].items():
            uint(value, 32 if name in ("retention_budget", "tras_max", "trefi") else 16, k + "." + name)
    if p["ck_ps"] != 1250 or type(p["ratio"]) is not int or (p["ratio"], p["bank_mode"]) not in ((4, "BG"), (2, "16B")):
        raise ValueError("unqualified speed/bank matrix")
    r = p["ratio"]
    if type(p["ranks"]) is not int or p["ranks"] not in (1, 2):
        raise ValueError("ranks")
    for key in ("read_link", "write_link", "always_on", "read_dbi", "write_dbi", "data_mask"):
        if type(p[key]) is not bool:
            raise ValueError(key)
    dev = p["device"]
    if set(dev) != {"mr8", "mr27", "mr57", "raw_parity", "read_link", "write_link"} or dev["mr8"] != 16:
        raise ValueError("only discovered 8Gb x16 device")
    for key in ("raw_parity", "read_link", "write_link"):
        if type(dev[key]) is not bool:
            raise ValueError("capability " + key)
    for key in ("mr27", "mr57"):
        uint(dev[key], 8, key)
    if dev["mr27"] & 1 and not (dev["mr27"] >> 1 & 31):
        raise ValueError("RFM threshold zero")
    if dev["mr57"] >> 4 or (dev["mr57"] >> 2 & 3) not in (0, 1):
        raise ValueError("single-bank RFM extension not admitted")
    if (p["read_link"] or p["write_link"]) and (r != 4 or not dev["raw_parity"]):
        raise ValueError("link PHY transport unavailable")
    if any(p[k] and not dev[k] for k in ("read_link", "write_link")):
        raise ValueError("DRAM link unsupported")
    if p["read_link"] and p["read_dbi"] or not p["data_mask"]:
        raise ValueError("DBI/mask option")
    mr = p["mr"]
    if set(mr) != {str(x) for x in MRS}:
        raise ValueError("MR image incomplete")
    for k, v in mr.items():
        uint(v, 8, "MR" + k)
    # Entire images for the restricted option set, not only latency bit fields.
    expected = {str(x): 0 for x in MRS}
    expected.update(
        {
            "1": 176 if r == 4 else 80,
            "2": 187 if r == 4 else 85,
            "3": (6 if r == 4 else 22) + 64 * int(p["read_dbi"]) + 128 * int(p["write_dbi"]),
            "13": 32,
            "18": (8 if r == 4 else 128) + 16 * int(p["always_on"]),
            "22": 64 * int(p["read_link"]) + 16 * int(p["write_link"]),
        }
    )
    if mr != expected:
        raise ValueError("MR image disagrees with qualified options")
    if (p["rl_ck"], p["wl_ck"], p["nwr_ck"]) != (
        (19 if p["read_link"] else 18 if p["read_dbi"] else 17, 9, 31 if p["write_link"] else 28)
        if r == 4
        else (20 if p["read_dbi"] else 18, 10, 28)
    ):
        raise ValueError("latency/MR row")
    phy = p["phy_half_wck"]
    d = p["dfi_wck"]
    m = p["management_ck"]
    t = p["dram_ck"]
    if phy["trained_phase"] != 0:
        raise ValueError("fixture requires trained zero phase")
    if phy["command"] != m["tctrl_delay"] * 2 * r or m["tcmd_lat"] != 0:
        raise ValueError("command path inconsistent")
    if any(p["wck"][k] for k in ("twck_toggle_cs", "twck_toggle_rd", "twck_toggle_wr")):
        raise ValueError("static restart/multi-CS synchronization not admitted")
    if m["tctrlupd_min"] < 2 or m["tctrlupd_max"] < m["tctrlupd_min"] + 2:
        raise ValueError("update release margin")
    if m["tphyupd_resp"] < m["tctrlupd_max"] + d["twrdata_delay"] // r + 2:
        raise ValueError("competing request response budget")
    if (
        m["tphymstr_resp"] < m["tphyupd_resp"]
        or max(m["tphymstr_type" + str(i)] for i in range(4)) + t["trfc_ab"] >= m["retention_budget"]
    ):
        raise ValueError("PHY master retention budget")
    minima = {
        "trcd": 15,
        "trp_pb": 15,
        "trp_ab": 17,
        "tras_min": 34,
        "trc": 51,
        "trrd": 4,
        "tfaw": 16,
        "twr": 31 if p["write_link"] else 28,
        "twtr_s": 9 if p["write_link"] else 5,
        "twtr_l": 13 if p["write_link"] else 10,
        "trfc_ab": 168,
        "trfc_pb": 96,
        "trfm_ab": 168,
        "trfm_pb": 136,
        "tpbr2pbr": 72,
        "tpbr2act": 6,
        "tzqcal": 1200,
        "tzqlat": 24,
        "tmrd": 12,
        "tmrr": 4 if r == 4 else 8,
        "tmrw": 8,
        "tfc": 201,
        "tckfspe": 6,
        "tckfspx": 6,
    }
    if (
        any(t[k] < v for k, v in minima.items())
        or not 1 <= t["trefi"] <= 3124
        or not t["tras_min"] <= t["tras_max"] <= 56160
    ):
        raise ValueError("DRAM timing out of qualified limits")
    if t["txsr"] < t["trfc_ab"] + 6 or t["trbtp"] < (4 if r == 4 else 2):
        raise ValueError("exit/precharge timing")
    if any(v == 0 for v in t.values()):
        raise ValueError("zero DRAM timing")


@dataclass(frozen=True)
class Waveform:
    ratio: int
    direction: str
    beats: int
    rank: int
    cas: int
    command: int
    enable: int
    slow: int
    fast: int
    first: int
    data: tuple[int, ...]
    ws_off: int | None
    stop: int
    disabled: int
    edges: tuple[int, ...]
    cs: tuple[tuple[int, int], ...]
    dfi_enable: tuple[int, ...]
    dfi_data: tuple[int, ...]
    returned: tuple[int, ...]
    data_cs: tuple[tuple[int, int], ...]


def waveform(p: dict, direction: str, beats: int, rank: int = 0, fast_sync: bool = False) -> Waveform:
    admit(p)
    if direction not in ("read", "write") or beats not in (16, 32) or not 0 <= rank < p["ranks"]:
        raise ValueError("operation")
    r = p["ratio"]
    ck = 2 * r
    phy = p["phy_half_wck"]
    w = p["wck"]
    d = p["dfi_wck"]
    cas = phy["command"]
    enable = 2 * w["twck_en_fs" if fast_sync else "twck_en_rd" if direction == "read" else "twck_en_wr"] + phy["wck"]
    slow = enable + 2 * w["twck_toggle"]
    fast = slow + 2 * w["twck_fast_toggle"]
    # Standalone fast sync completes before the access. Adjacent paired forms
    # use one CK; this fixture waits twelve CK for the standalone form.
    command = cas + (12 if fast_sync else 1) * ck
    first = (
        command
        + p["rl_ck" if direction == "read" else "wl_ck"] * ck
        + phy["dq_output" if direction == "read" else "dq_input"]
    )
    data = tuple(first + i + (16 if r == 4 and beats == 32 and i >= 16 else 0) for i in range(beats))
    end = data[-1] + 1
    ws_off = ((end + 2 * w["twck_toggle_post"] + ck - 1) // ck) * ck if p["always_on"] else None
    stop = ws_off + 5 * ck if ws_off is not None else end + 2 * w["twck_toggle_post"]
    disabled = (ws_off - cas if ws_off is not None else command - cas) + 2 * w["twck_dis"] + phy["wck"]
    offsets = [2 * i + (16 if r == 4 and beats == 32 and i >= 8 else 0) for i in range(beats // 2)]
    dfi_command = command - cas
    en = tuple(dfi_command + 2 * d["trddata_en" if direction == "read" else "tphy_wrlat"] + x for x in offsets)
    driven = tuple(x + 2 * d["tphy_wrdata"] for x in en) if direction == "write" else ()
    # Each word returns on a CK sampling edge after its last physical beat and
    # the explicitly modeled deserializer path. Gaps retain burst word identity.
    returned = (
        tuple(((data[2 * i + 1] + phy["read"] + ck - 1) // ck) * ck for i in range(beats // 2))
        if direction == "read"
        else ()
    )
    return Waveform(
        r,
        direction,
        beats,
        rank,
        cas,
        command,
        enable,
        slow,
        fast,
        first,
        data,
        ws_off,
        stop,
        disabled,
        tuple(range(slow, fast, 2)) + tuple(range(fast, stop)),
        ((cas, rank), (disabled, rank)),
        en,
        driven,
        returned,
        tuple(
            (dfi_command + 2 * d["tphy_rdcslat" if direction == "read" else "tphy_wrcslat"] + x, rank) for x in offsets
        ),
    )
