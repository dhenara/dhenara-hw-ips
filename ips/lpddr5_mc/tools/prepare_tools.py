"""Prepare the pinned simulator and Python checks without system privileges."""

import hashlib
import os
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

VERSION = "5.006-3"
URL = "https://deb.debian.org/debian/pool/main/v/verilator/verilator_5.006-3_amd64.deb"
SHA256 = "97239c73375f4e3c6d5e04afd7afdcae133e1b25bf0d876f81af04f09961c6bf"


def main() -> None:
    if platform.machine() != "x86_64":
        raise SystemExit("This pinned binary recipe supports Linux amd64 only")
    root = Path(os.environ["DHENARA_AGENT_TOOL_STORAGE_ROOT"]) / "lpddr5-contracts" / ("verilator-" + VERSION)
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "package.deb"
    if not archive.exists():
        with urllib.request.urlopen(URL, timeout=60) as response:
            archive.write_bytes(response.read())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise SystemExit("Simulator package checksum mismatch")
    subprocess.run(["dpkg-deb", "-x", str(archive), str(root)], check=True)
    subprocess.run([str(root / "usr/bin/verilator"), "--version"], check=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--target",
            str(root.parent / "python-tools"),
            "-r",
            str(Path(__file__).with_name("requirements-dev.txt")),
        ],
        check=True,
        env={**os.environ, "PIP_CACHE_DIR": str(root.parent / "pip-cache")},
    )


if __name__ == "__main__":
    main()
