"""Run a compiler or simulator with its descendants under one deadline."""

import os
import signal
import subprocess
from pathlib import Path
from types import FrameType
from typing import TextIO


class RunInterrupted(SystemExit):
    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__(128 + signum)

    def __str__(self) -> str:
        return f"interrupted by {signal.Signals(self.signum).name}"


def run_group(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    output: TextIO,
    timeout: float,
    timeout_signal: int = signal.SIGTERM,
) -> tuple[int, bool]:
    process: subprocess.Popen | None = None
    interrupted = 0
    cleaning = False

    def on_signal(signum: int, _frame: FrameType | None) -> None:
        nonlocal interrupted
        interrupted = signum
        # Popen must first return the new process-group identity, even if cancellation
        # arrives while it is starting. Cleanup also needs to survive repeated signals.
        if process is not None and not cleaning:
            raise RunInterrupted(signum)

    previous = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGINT, signal.SIGTERM)}
    timed_out = False
    finished = False
    try:
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True
        )
        if interrupted:
            raise RunInterrupted(interrupted)
        try:
            process.wait(timeout=timeout)
            finished = True
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        cleaning = True
        try:
            if process is not None and not finished:
                stop_signal = interrupted or timeout_signal
                try:
                    os.killpg(process.pid, stop_signal)
                    if stop_signal != signal.SIGKILL:
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            pass
                        # The launcher can exit while a simulator still ignores TERM.
                        os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    if interrupted:
        raise RunInterrupted(interrupted)
    assert process is not None and process.returncode is not None
    return process.returncode, timed_out
