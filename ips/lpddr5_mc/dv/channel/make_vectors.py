"""Independent RTL stimuli/expectations from the accepted pin and ECC oracles."""

import json
import sys
from pathlib import Path

IP = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(IP / "dv"))
from contracts.dvfs import Sample, check_dvfs  # noqa: E402
from contracts.integrity import transport  # noqa: E402
from contracts.profiles import waveform  # noqa: E402
from contracts.read_completion import CompletionOracle, ExpectedWord, ReadBusOracle  # noqa: E402
from contracts.update import check_update  # noqa: E402


def fields(profile: dict) -> dict:
    result = {
        k: profile[k]
        for k in (
            "ratio",
            "ranks",
            "read_link",
            "write_link",
            "always_on",
            "read_dbi",
            "write_dbi",
            "data_mask",
            "rl_ck",
            "wl_ck",
            "nwr_ck",
        )
    }
    result.update({k: profile["device"][k] for k in ("mr8", "mr27", "mr57", "raw_parity")})
    result.update({f"device_{k}": profile["device"][k] for k in ("read_link", "write_link")})
    for group in ("wck", "phy_half_wck", "dfi_wck", "management_ck", "dram_ck"):
        result.update({f"{group}_{k}": v for k, v in profile[group].items()})
    return result


def generate(destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    update_rows = []
    for assertion in (-1, 0, 1, 2, 3, 4, 5, 31):
        for release in (2, 3, 4, 5, 29, 30, 31, 32, 36):
            if assertion >= release:
                continue
            ack = [assertion >= 0 and assertion <= edge < release for edge in range(40)]
            outcome = check_update(4, 32, ack)
            update_rows.append(f"{assertion} {release} {outcome.release or -1} {int(outcome.fault is not None)}")
    (destination / "updates.txt").write_text("\n".join(update_rows) + "\n")
    profiles = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"]
    rows = json.loads((IP / "config/reference/link_ecc_matrix.json").read_text())
    source = [
        "task automatic set_profile(input int profile_number);",
        "  profile='0;",
        "  case (profile_number)",
    ]
    for i, p in enumerate(profiles):
        source.append(f"    {i}: begin")
        source.extend(f"      profile.{k}={int(v)};" for k, v in fields(p).items())
        source.extend(f"      profile.mr[{k}]=8'h{v:02x};" for k, v in p["mr"].items() if v)
        source.append("    end")
    source.extend(['    default: $fatal(1,"bad test profile");', "  endcase", "endtask"])
    (destination / "profiles.svh").write_text("\n".join(source) + "\n")
    cases = []
    for pi, p in enumerate(profiles):
        ratio = p["ratio"]
        ck = ratio * 2
        for write in (False, True):
            for beats in (16, 32):
                rank = (pi + beats // 16) % 2
                data = bytes(((i * 37 + pi * 19 + 3) % 256) for i in range(beats * 2))
                enables = [not (write and beats == 16 and i % 5 == 0) for i in range(beats * 2)]
                wf = waveform(p, "write" if write else "read", beats, rank)
                # The reference's access is at one CK. RTL launch is time zero.
                en = [(x - ck) // 2 for x in wf.dfi_enable]
                driven = [(x - ck) // 2 for x in wf.dfi_data]
                cs_start = (wf.data_cs[0][0] - ck) // 2
                cs_end = (wf.data_cs_end - ck) // 2
                if write:
                    wire = transport(rows, pi, data, enables, write=True, dbi=p["write_dbi"])
                    by_byte = {(b.beat, b.byte): b for b in wire}
                elif p["read_link"]:
                    wire = transport(rows, pi, data, enables, write=False)
                    by_byte = {(b.beat, b.byte): b for b in wire}
                else:
                    by_byte = {}
                returned = {}
                if not write:
                    expected = [ExpectedWord(en[w], pi, w, b) for b in range(2) for w in range(beats // 2)]
                    completion = CompletionOracle(expected, beats // 2, p["dfi_wck"]["tphy_rdlat"])
                    oracle = ReadBusOracle(ratio, completion)
                    pointer = [0, 0]
                    for b in range(2):
                        for w, at in enumerate(wf.returned):
                            cycle = (at - ck) // ck + b + int(w >= 8 and not p["read_link"])
                            lane = pointer[b] % ratio
                            pointer[b] += 1
                            returned.setdefault(cycle, []).append((lane, b, w))
                    for cycle, actions in sorted(returned.items()):
                        lanes = [[None, None] for _ in range(ratio)]
                        dnv = [[0, 0] for _ in range(ratio)]
                        for lane, b, w in actions:
                            lanes[lane][b] = data[w * 4 + b] | data[w * 4 + 2 + b] << 8
                        oracle.snapshot(cycle, lanes, dnv)
                    assert not any(completion.pending.values())
                pin_end = wf.data[-1] + 1 - ck
                safe_end = pin_end
                if write:
                    # Conservative output-path drain, separately from pin end.
                    d = p["dfi_wck"]
                    safe_end = max(
                        pin_end,
                        2
                        * (
                            d["tphy_wrlat"]
                            + d["tphy_wrdata"]
                            + d["twrdata_delay"]
                            + beats // 2
                            + (8 if ratio == 4 and beats == 32 else 0)
                        ),
                    )
                done_at = max(
                    (safe_end + ck - 1) // ck,
                    max(returned, default=0) + (0 if write else 1),
                )
                lines = []
                for cycle in range(1, done_at + 5):
                    ret_data = ret_valid = ret_dnv = ret_parity = 0
                    for lane, b, w in returned.get(cycle, []):
                        ret_valid |= 1 << (lane * 2 + b)
                        for beat in range(2):
                            value = data[w * 4 + beat * 2 + b]
                            parity = by_byte[(w * 2 + beat, b)].parity if p["read_link"] else 0
                            if p["read_dbi"] and value.bit_count() > 4:
                                value ^= 255
                                parity = 1
                            ret_data |= value << (lane * 32 + beat * 16 + b * 8)
                            ret_parity |= parity << (lane * 4 + beat * 2 + b)
                    wd = wm = we = wc = wp = re = rc = 0
                    for lane in range(ratio):
                        at = cycle * ratio + lane
                        if at in en:
                            if write:
                                we |= 3 << (lane * 2)
                            else:
                                re |= 3 << (lane * 2)
                        if write and at in driven:
                            w = driven.index(at)
                            for beat in range(2):
                                for b in range(2):
                                    wire_byte = by_byte[(w * 2 + beat, b)]
                                    wd |= wire_byte.dq << (lane * 32 + beat * 16 + b * 8)
                                    wm |= wire_byte.dmi << (lane * 4 + beat * 2 + b)
                                    wp |= (wire_byte.parity if p["write_link"] else 0) << (lane * 4 + beat * 2 + b)
                        if cs_start <= at < cs_end:
                            if write:
                                wc |= (1 << rank) << (lane * 2)
                            else:
                                rc |= ((1 << rank) | (1 << rank) << 2) << (lane * 4)
                    values = [
                        cycle,
                        ret_data,
                        ret_valid,
                        ret_dnv,
                        ret_parity,
                        wd,
                        wm,
                        we,
                        wc,
                        wp,
                        re,
                        rc,
                        int(cycle >= done_at),
                    ]
                    lines.append(" ".join(f"{v:x}" for v in values))
                index = len(cases)
                (destination / f"data-{index}.txt").write_text("\n".join(lines) + "\n")
                wck_lines = []
                end_cycle = (wf.disabled - p["phy_half_wck"]["wck"] + ck - 1) // ck + 3
                off_cycle = (wf.ws_off - p["phy_half_wck"]["command"]) // ck if wf.ws_off is not None else -1
                for cycle in range(1, end_cycle + 1):
                    wck_enable = wck_cs = wck_toggle = 0
                    for phase in range(ratio):
                        at = (cycle * ratio + phase) * 2 + p["phy_half_wck"]["wck"]
                        for b in range(2):
                            if at < wf.disabled:
                                wck_cs |= (1 << rank) << (phase * 4 + b * 2)
                                if at >= wf.enable:
                                    wck_enable |= 1 << (phase * 2 + b)
                                toggle = 0 if at < wf.slow or at >= wf.stop else 2 if at < wf.fast else 3
                                wck_toggle |= toggle << (phase * 4 + b * 2)
                    wck_lines.append(f"{cycle:x} {int(cycle == off_cycle):x} {wck_enable:x} {wck_cs:x} {wck_toggle:x}")
                (destination / f"wck-{index}.txt").write_text("\n".join(wck_lines) + "\n")
                cases.append(
                    {
                        "profile": pi,
                        "write": write,
                        "beats": beats,
                        "rank": rank,
                        "data": int.from_bytes(data, "little"),
                        "enables": sum(int(v) << i for i, v in enumerate(enables)),
                        "done_at": done_at,
                        "pin_end": pin_end,
                    }
                )
    source = []
    for i, case in enumerate(cases):
        source.append(
            f"run_case({i},{case['profile']},{int(case['write'])},{case['beats']},{case['rank']},512'h{case['data']:0128x},64'h{case['enables']:016x});"
        )
    (destination / "cases.svh").write_text("\n".join(source) + "\n")
    (destination / "cases.txt").write_text(
        "\n".join(
            f"{i:x} {c['profile']:x} {int(c['write']):x} {c['beats']:x} {c['rank']:x} {c['data']:x} {c['enables']:x}"
            for i, c in enumerate(cases)
        )
        + "\n"
    )
    # Two BL32 commands two CK apart fill each other's split gaps. The
    # expected stream follows physical words, not complete command order.
    p = profiles[0]
    wf = waveform(p, "read", 32)
    ratio = p["ratio"]
    ck = 2 * ratio
    payloads = [bytes((i * 37 + 3 + j * 71) % 256 for i in range(64)) for j in range(2)]
    expected = []
    actions_by_cycle = {}
    for b in range(2):
        stream = []
        for j, shift in enumerate((0, 2)):
            for w in range(16):
                enable = (wf.dfi_enable[w] - ck) // 2 + shift * ratio
                returned_at = (wf.returned[w] - ck) // ck + shift + b
                stream.append((returned_at, enable, j, w))
                expected.append(ExpectedWord(enable, j + 1, w, b))
        for ordinal, (at, _enable, j, w) in enumerate(sorted(stream)):
            actions_by_cycle.setdefault(at, []).append((ordinal % ratio, b, j, w))
    expected.sort(key=lambda item: (item.slice_index, item.enable))
    completion = CompletionOracle(expected, 16, p["dfi_wck"]["tphy_rdlat"])
    oracle = ReadBusOracle(ratio, completion)
    multi_lines = []
    for cycle in range(1, 48):
        ret_data = ret_valid = ret_parity = 0
        lanes = [[None, None] for _ in range(ratio)]
        for lane, b, j, w in actions_by_cycle.get(cycle, []):
            value = payloads[j][w * 4 + b] | payloads[j][w * 4 + b + 2] << 8
            lanes[lane][b] = value
            ret_data |= (value & 255) << (lane * 32 + b * 8)
            ret_data |= (value >> 8) << (lane * 32 + b * 8 + 16)
            ret_valid |= 1 << (lane * 2 + b)
        oracle.snapshot(cycle, lanes, [[0, 0] for _ in range(ratio)])
        multi_lines.append(f"{cycle:x} {ret_data:x} {ret_valid:x}")
    assert not any(completion.pending.values())
    (destination / "multi.txt").write_text("\n".join(multi_lines) + "\n")
    (destination / "multi.svh").write_text(
        "\n".join(f"multi_data[{i}]=512'h{int.from_bytes(v, 'little'):0128x};" for i, v in enumerate(payloads)) + "\n"
    )
    # Independent seeded reference payload/check vectors; RTL exhausts all
    # singles and doubles for each of these codewords.
    import random

    from contracts.integrity import inline_encode
    from contracts.link_ecc import encode

    rng = random.Random(2095)
    with (destination / "ecc.txt").open("w") as out:
        for _ in range(8):
            data = rng.getrandbits(128)
            code = inline_encode(data & ((1 << 64) - 1))
            bits = [(code >> ((1 << i) - 1)) & 1 for i in range(7)] + [(code >> 71) & 1]
            checks = sum(b << i for i, b in enumerate(bits))
            dmi = rng.getrandbits(16)
            out.write(
                f"{data:x} {encode(rows['data_rows'], data):x} {checks:x} {dmi:x} {encode(rows['dmi_rows'], dmi):x}\n"
            )
    return {
        "profiles": len(profiles),
        "transfer_cases": len(cases),
        "ecc_payloads": 8,
        "seed": 2095,
    }


if __name__ == "__main__":
    print(json.dumps(generate(Path(sys.argv[1]))))


def validate_fsp(directory: Path) -> dict:
    results = {}
    for number, expected in ((0, "acknowledged"), (1, "declined")):
        samples = []
        for row in (directory / f"fsp-{number}.txt").read_text().splitlines():
            start, complete, frequency, ratio, fsp, changed, disabled = map(int, row.split())
            samples.append(Sample(bool(start), bool(complete), frequency, ratio, fsp, bool(changed), bool(disabled)))
        outcome = check_dvfs(4, 128, samples, drained=True)
        if outcome.disposition != expected:
            raise AssertionError(f"FSP trace {number}: {outcome}")
        results[str(number)] = {"disposition": outcome.disposition, "returned_edge": outcome.returned_edge}
    return results
