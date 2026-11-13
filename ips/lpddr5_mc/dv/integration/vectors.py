"""Create independent software-image/profile expectations from canonical JSON."""

import json
import sys
from pathlib import Path

IP = Path(__file__).resolve().parents[2]


def generate(destination: Path) -> None:
    sys.path.insert(0, str(IP / "dv"))
    from contracts.configuration import example_configuration, register_image
    from contracts.raw_parity import generate_read_encoder

    destination.mkdir(parents=True, exist_ok=True)
    generate_read_encoder(destination)
    profiles = json.loads((IP / "config/contracts/profiles.json").read_text())["profiles"]
    schema = json.loads((IP / "config/contracts/registers.json").read_text())
    include = ["task automatic expected_profile(input int number);", "  profile='0;", "  case(number)"]
    rows = [str(len(profiles) * 2 * 3)]
    for index, profile in enumerate(profiles):
        include.append(f"    {index}: begin")
        for key in (
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
        ):
            include.append(f"      profile.{key}={int(profile[key])};")
        for key in ("mr8", "mr27", "mr57", "raw_parity"):
            include.append(f"      profile.{key}={int(profile['device'][key])};")
        for key in ("read_link", "write_link"):
            include.append(f"      profile.device_{key}={int(profile['device'][key])};")
        for group in ("wck", "phy_half_wck", "dfi_wck", "management_ck", "dram_ck"):
            for key, value in profile[group].items():
                include.append(f"      profile.{group}_{key}={value};")
        for key, value in profile["mr"].items():
            include.append(f"      profile.mr[{key}]={value};")
        include.append("    end")
        for ranks in (1, 2):
            for fsp in range(3):
                config = example_configuration()
                config.update(
                    ranks=ranks,
                    ratio=profile["ratio"],
                    bank_mode=profile["bank_mode"],
                    read_link=profile["read_link"],
                    write_link=profile["write_link"],
                    phy_raw_parity=True,
                )
                image = register_image(schema, profile, config, fsp)
                image["DVFS"] = fsp
                rows.append(f"{index} {ranks} {fsp}")
                rows.extend(f"{image[reg['name']]:08x}" for reg in schema["registers"])
    # The wrapper final-tail deadline is generated from the selected profile;
    # no independent magic loop bound lives in the HDL fixture.
    from wrapper_management import final_tail_budget, profile_binding

    tail = final_tail_budget(profile_binding())
    include += ['    default: $fatal(1,"unknown profile");', "  endcase", "endtask"]
    (destination / "wrapper_tail.svh").write_text(
        f"localparam int WM_FINAL_TAIL_CK={tail['total_ck']};\n"
        f"localparam int WM_FINAL_PAGE_IDLE_CK={tail['page_idle_ck']};\n"
    )
    (destination / "expected_profiles.svh").write_text("\n".join(include) + "\n")
    (destination / "images.txt").write_text("\n".join(rows) + "\n")
