"""Validate coverage references against actual tests without inventing closure."""

import ast
import json
from pathlib import Path

from system.host import require


def declared_tests(folder: Path) -> set[str]:
    tests = set()
    for path in folder.glob("test_*.py"):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.ClassDef):
                tests.update(
                    f"{path.stem}.{node.name}.{method.name}"
                    for method in node.body
                    if isinstance(method, ast.FunctionDef) and method.name.startswith("test_")
                )
    return tests


def qualify(observations: list[dict]) -> dict:
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "coverage.json").read_text())
    require(manifest["version"] == 1 and not manifest["controller_qualified"], "coverage version/scope")
    executed = {t["test"]: t["verdict"] for t in observations}
    foundation = declared_tests(root.parent / "tests")
    keys, requirements = set(), set()
    for row in manifest["entries"]:
        require(row["key"] not in keys and row["behaviors"] and row["pending"], "coverage identity/behavior/gap")
        require(row["combined_status"] == "pending", "fixture cannot establish combined closure")
        keys.add(row["key"])
        requirements.add(row["requirement"])
        require(all(executed.get(t) == "passed" for t in row["environment_tests"]), "missing/failed coverage test")
        require(set(row["foundation_tests"]) <= foundation, "unknown foundation test reference")
    require(
        requirements
        == {
            "functional_controller",
            "lpddr_protocol",
            "ecc_reliability",
            "control_lifecycle",
            "host_bandwidth",
            "reusable_delivery",
        },
        "missing accepted requirement family",
    )
    return {
        "entries": manifest["entries"],
        "controller_qualified": False,
        "foundation_references": "declared; execution is recorded separately by the full runner",
    }
