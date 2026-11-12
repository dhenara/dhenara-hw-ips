"""Continuous transition and serialized inline-owner observations.

Pin structure/WCK observation never stops at a software fault marker. Refresh
obligations suspend explicitly when retention is lost; only a witnessed reset
and fresh training/profile can start a new trusted epoch. Internal inline
results establish owner dependencies, not independent DRAM pin completion.
The existing pin/data replay must accompany a connected acceptance run.
"""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from contracts.integrity import inline_decode
from contracts.lifecycle import Maintenance
from system.dram import decode
from system.ecc import InlineWitness
from system.host import require
from system.trace import number
from wck_observations import WckObservations

DATA_BYTES = (1073741824 // 576) * 512


def identity(e):
    return tuple(e[k] for k in ("tag", "generation", "boot", "config"))


def offset(e):
    return (e["row"] << 15) | (e["bank"] << 11) | (e["column"] << 5)


class InlineDrain:
    """One real lock, including the entire shared check line, until completion.

    Versions are independent counters at this observation boundary. Serial
    ownership prevents an old correction from writing through a newer version.
    No cache replacement, hit or eviction event is inferred from this owner.
    """

    def __init__(self):
        self.live = self.pending = None
        self.poison = set()
        self.versions = {}
        self.completed = self.scrubs = self.failed = self.aborted = 0
        self.dirty = False

    def reset(self):
        self.aborted += self.live is not None
        self.live = self.pending = None
        self.poison.clear()
        self.versions.clear()
        self.dirty = False

    def event(self, e):
        kind = e["kind"]
        if kind == "inline_admit":
            require(self.live is None, "second operation before inline lock release")
            at = offset(e)
            require(
                e["bytes"] in (32, 64) and at < DATA_BYTES,
                "inline reservation/capacity",
            )
            self.live = {
                "request": e,
                "at": at & ~63,
                "check": (DATA_BYTES + at // 8) & ~63,
                "index": (at & ~63) // 8 % 64,
                "stage": 0,
                "failed": False,
                "data": None,
                "checks": None,
                "expected": None,
                "corrected": False,
            }
            self.dirty = False
            return
        t = self.live
        require(t is not None, "inline event without live original")
        if kind == "inline_physical":
            require(
                self.pending is None and not t["failed"],
                "inline duplicate/failed physical issue",
            )
            stage = t["stage"]
            require(
                stage < 4 and bool(e["write"]) == (stage >= 2),
                "inline data/check order",
            )
            require(
                offset(e) == t["check" if stage in (0, 3) else "at"]
                and e["rank"] == t["request"]["rank"]
                and e["bytes"] == 64
                and number(e["mask"]) == (1 << 64) - 1,
                "inline physical location/mask",
            )
            if stage >= 2:
                require(t["expected"] is not None, "inline write before independent decode")
                data = t["expected"] if stage == 2 else t["expected_check"]
                require(
                    number(e["data"]).to_bytes(64, "little") == bytes(data),
                    "inline data or shared check-line preservation",
                )
            self.pending = e
        elif kind == "inline_result":
            require(
                self.pending is not None,
                "inline result without actual physical request",
            )
            q = self.pending
            require(
                identity(q) == identity(e) and q["generation"] == 0 and q["write"] == e["write"],
                "stale inline physical result",
            )
            okay = e["physical"] and e["integrity"] and not e["failed"] and not e["response"]
            stage = t["stage"]
            if not okay:
                t["failed"] = True
                if stage >= 2:
                    self.poison.update(self.words(t))
            elif stage == 0:
                t["checks"] = number(e["data"]).to_bytes(64, "little")
            elif stage == 1:
                self.prepare(t, number(e["data"]).to_bytes(64, "little"))
            elif stage == 2:
                self.dirty = True
            elif stage == 3:
                self.dirty = False
                self.poison.difference_update(self.words(t))
            self.pending = None
            t["stage"] += 1
        elif kind == "inline_complete":
            require(
                identity(e) == identity(t["request"]) and self.pending is None,
                "inline original completion identity",
            )
            success = not e["failed"] and not e["response"]
            require(
                not success
                or e["physical"]
                and e["integrity"]
                and not t["failed"]
                and t["stage"] == (4 if t["request"]["write"] or t["corrected"] else 2)
                and not self.dirty,
                "inline success before physical data/check/integrity settlement",
            )
            require(success or e["failed"] and e["response"] == 2, "inline failure status")
            if success:
                for word in self.words(t):
                    self.versions[word] = self.versions.get(word, 0) + int(t["stage"] == 4)
                self.scrubs += bool(t["corrected"] and not t["request"]["write"])
            else:
                self.failed += 1
            self.live = None
            self.dirty = False
            self.completed += 1
        else:
            raise ValueError("unknown inline event")

    @staticmethod
    def words(t):
        q = t["request"]
        first = 0 if q["bytes"] == 64 else (q["column"] & 1) * 4
        return [(q["rank"], t["at"] // 8 + w) for w in range(first, first + q["bytes"] // 8)]

    def prepare(self, t, raw):
        q = t["request"]
        result, checks = bytearray(raw), bytearray(t["checks"])
        first = 0 if q["bytes"] == 64 else (q["column"] & 1) * 4
        for w in range(first, first + q["bytes"] // 8):
            source = w - first
            mask = number(q["mask"]) >> (source * 8) & 255
            value = int.from_bytes(raw[w * 8 : w * 8 + 8], "little")
            if not q["write"] or mask != 255:
                value, status = inline_decode(InlineWitness.codeword(value, checks[t["index"] + w]))
                if status == "uncorrectable" or (q["rank"], t["at"] // 8 + w) in self.poison:
                    t["failed"] = True
                t["corrected"] |= status == "corrected"
            if q["write"]:
                new = number(q["data"]) >> (source * 64)
                value = sum(((new if mask >> b & 1 else value) >> (b * 8) & 255) << (b * 8) for b in range(8))
            result[w * 8 : w * 8 + 8] = value.to_bytes(8, "little")
            checks[t["index"] + w] = InlineWitness.check_byte(value)
        t["expected"], t["expected_check"] = result, checks


class Channel:
    def __init__(self, profile, epoch_profiles=None):
        self.p = copy.deepcopy(profile)
        self.epoch_profiles = epoch_profiles or {}
        self.wck_history = []
        self.last = None
        self.wck = WckObservations(self.p)
        self.wck_origin = 0
        self.pair = None
        self.inline = InlineDrain()
        self.maintenance = None
        self.suspended = []
        self.suspend_start = 0
        self.retired_boot = -1
        self.reset_seen = True
        self.reset_count = self.samples = self.commands = self.trusted_ck = 0
        self.entries = {k: 0 for k in ("update", "phy_update", "fsp", "power", "master")}
        self.dirty_requests = {k: 0 for k in ("update", "fsp", "power", "master")}
        self.request_pending = set()

    def sample(self, e):
        ck = e["ck"]
        old = self.last
        require(ck == (old["ck"] + 1 if old else 0), "missing/repeated continuous CK")
        require(
            old is None or e["ps"] - old["ps"] == self.p["ck_ps"],
            "continuous CK timestamp",
        )
        self.samples += 1
        reset = not e["reset_n"] or bool(e["destructive"])
        if e["destructive"]:
            require(
                e["settled"] and e["confirmed"] and not e["enabled"],
                "destructive reset before settlement/confirmation",
            )
        if reset and (old is None or old["reset_n"] and not old["destructive"]):
            self.reset_seen = True
            self.reset_count += 1
            self.retired_boot = old["boot"] if old else -1
            self.wck = WckObservations(self.p)
            self.wck_origin = ck
            self.pair = None
            self.inline.reset()
            self.maintenance = None
        if reset:
            self.wck_origin = ck + 1
        require(
            not e["memory_reset"] or reset or not e["trusted"],
            "external memory reset while trusted",
        )
        if old and e["boot"] != old["boot"]:
            require(
                self.reset_seen and e["boot"] == old["boot"] + 1 and not e["enabled"],
                "unwitnessed boot epoch change",
            )
        if e["trusted"] and not (old and old["trusted"]):
            require(
                not e["fault"]
                and e["trained"]
                and e["training_epoch"] == e["boot"]
                and e["config_valid"]
                and e["config"] > 0
                and self.reset_seen
                and e["boot"] > self.retired_boot,
                "trust without fresh reset/training/profile",
            )
            self.reset_seen = False
            self.suspended.append({"start": self.suspend_start, "end": ck, "reason": "untrusted memory"})
            self.suspend_start = None
            self.maintenance = Maintenance(
                ranks=self.p["ranks"],
                mr27=self.p["device"]["mr27"],
                mr57=self.p["device"]["mr57"],
            )
            self.maintenance.now = ck
            self.maintenance.last_refresh = [ck] * self.p["ranks"]
        if old and old["trusted"] and not e["trusted"]:
            self.suspend_start = ck
            self.maintenance = None
        require(
            not e["enabled"] or e["trusted"] and not e["fault"] and e["trained"] and e["config_valid"],
            "enabled untrusted service",
        )
        if old and e["config"] != old["config"] and e["config_valid"]:
            require(
                not self.inline.live and e["drained"],
                "configuration changed with live integrity lock",
            )
            target = self.epoch_profiles.get(e["config"], self.p)
            if target != self.p:
                require(
                    not number(e["wck"]) and self.wck.epoch is None and target["ck_ps"] == self.p["ck_ps"],
                    "profile switch before WCK disable or unsupported CK change",
                )
                self.wck_history.append(self.wck.finish())
                self.p = copy.deepcopy(target)
                self.wck = WckObservations(self.p)
                self.wck_origin = ck
        if not reset:
            if e["config_valid"] and e["ratio"] != self.p["ratio"]:
                raise ValueError("continuous WCK profile transition needs an explicitly supplied matching profile")
            self.wck.sample(ck - self.wck_origin, e["cs"], e["ca"], number(e["wck"]))
            self.ca(e)
        for kind in self.entries:
            if e[kind] and not (old and old[kind]):
                require(
                    e["drained"]
                    and e["pin_drained"]
                    and e["ecc_drained"]
                    and e["inline_idle"]
                    and not self.inline.live
                    and not self.inline.dirty
                    and number(e["wck"]) == 0,
                    "management entry before composed pin/data/check drain",
                )
                self.entries[kind] += 1
                self.request_pending.discard(kind)
        if e["command_valid"] and self.inline.live:
            kind = {3: "update", 4: "power", 5: "power", 7: "fsp"}.get(e["command"])
            if kind and self.inline.dirty and kind not in self.request_pending:
                self.dirty_requests[kind] += 1
                self.request_pending.add(kind)
        if e["drained"] or e["ecc_drained"]:
            require(
                not self.inline.live and not self.inline.dirty,
                "false integrity drained witness",
            )
        if e["owner"] == 1 and e["trained"]:
            require(
                (not e["cs"] or e.get("fsp_busy", 0) and decode(e["ca"]).kind in ("MRW1", "MRW2"))
                and number(e["wck"]) == 0
                and not (number(e["write"]) >> 24 & 255)
                and not (number(e["read"]) >> 16 & 255),
                "controller CA/WCK while PHY owns pins",
            )
        self.last = e

    def ca(self, e):
        op = decode(e["ca"]) if e["cs"] else None
        require(
            0 <= e["cs"] < (1 << self.p["ranks"]),
            "continuous CA rank",
        )
        if self.pair:
            kind, cs = self.pair
            require(
                op is not None and op.kind == kind and e["cs"] == cs,
                "continuous reserved CA pair",
            )
            self.pair = None
        elif op:
            require(op.kind not in ("ACT2", "MRW2"), "orphan continuous second command")
            if op.kind in ("ACT1", "MRW1"):
                self.pair = ("ACT2" if op.kind == "ACT1" else "MRW2", e["cs"])
        if op:
            self.commands += 1
            if e["fault"] or e["host_fault"]:
                require(
                    op.kind not in ("ACT1", "RD", "WR", "MWR"),
                    "new memory access after ownership/trust failure",
                )
        if e["trusted"]:
            self.trusted_ck += 1
            m = self.maintenance
            if e["thermal"]:
                m.temperature(e["thermal"])
            m.advance(e["ck"])
            if op:
                require(
                    op.kind not in ("ACT1", "ACT2", "RD", "WR", "MWR", "CAS", "MRR") or e["cs"] in (1, 2),
                    "multiple ranks on memory access",
                )
                for rank in range(self.p["ranks"]):
                    if not e["cs"] >> rank & 1:
                        continue
                    if op.kind == "ACT1":
                        m.activate(rank, op.bank)
                    elif op.kind == "REF":
                        m.refresh(
                            rank,
                            pair=None if op.all_banks else op.bank,
                            extra=m.extra[rank],
                        )
                    elif op.kind == "RFM":
                        m.rfm(rank, pair=None if op.all_banks else op.bank)
                    elif op.kind == "SRE":
                        m.hold_sr(rank)
                    elif op.kind == "SRX":
                        m.exit_sr(rank)
            for rank in range(self.p["ranks"]):
                if e["refresh_done"] >> rank & 1:
                    require(
                        e["master"] and e["refresh_request"] >> rank & 1,
                        "unsolicited PHY refresh completion",
                    )
                    m.refresh(rank)

    def finish(self):
        require(self.last is not None and self.pair is None, "unfinished continuous CA pair")
        require(self.inline.live is None, "unfinished inline lock at trace end")
        wck = self.wck.finish()
        intervals = list(self.suspended)
        if self.suspend_start is not None:
            intervals.append(
                {
                    "start": self.suspend_start,
                    "end": self.last["ck"] + 1,
                    "reason": "untrusted memory",
                }
            )
        return {
            "samples": self.samples,
            "commands": self.commands,
            "trusted_ck": self.trusted_ck,
            "refresh_suspended": intervals,
            "resets": self.reset_count,
            "wck": wck,
            "prior_wck_profiles": self.wck_history,
            "entries": self.entries,
            "dirty_requests": self.dirty_requests,
            "inline_completed": self.inline.completed,
            "inline_scrubs": self.inline.scrubs,
            "inline_failed": self.inline.failed,
            "inline_aborted_by_reset": self.inline.aborted,
        }


def check(events, profile, epoch_profiles=None):
    header = events[0]
    require(header["kind"] == "header" and header["version"] == 1, "continuous header")
    profile = {**profile, "ranks": header["ranks"]}
    bindings = {epoch: {**p, "ranks": header["ranks"]} for epoch, p in (epoch_profiles or {}).items()}
    channels = [Channel(profile, bindings) for _ in range(header["channels"])]
    ended = False
    for e in events[1:]:
        require(not ended, "continuous event after end")
        if e["kind"] == "end":
            require(
                all(c.last is not None and c.last["ck"] + 1 == e["ck"] for c in channels),
                "continuous missing final CK",
            )
            ended = True
        elif e["kind"] == "transition":
            channels[e["channel"]].sample(e)
        else:
            c = channels[e["channel"]]
            require(c.last and c.last["ck"] == e["ck"], "inline event outside continuous CK")
            c.inline.event(e)
    require(ended, "continuous trace missing end")
    return {
        "channels": [c.finish() for c in channels],
        "limitations": [
            "Internal inline results require independent CA/data/return pin replay for physical proof.",
            "Untrusted intervals retain structural CA/WCK checks; refresh retention is explicitly suspended.",
            "FSP changes require an explicit configuration-epoch to numeric-profile binding; no timing is guessed.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument(
        "--profiles",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "config/contracts/profiles.json",
    )
    parser.add_argument("--profile-number", type=int, default=0)
    parser.add_argument("--epoch-profile", action="append", default=[], metavar="EPOCH:PROFILE_NUMBER")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    require(not args.report.exists(), "refuse to overwrite continuous evidence")
    profiles = json.loads(args.profiles.read_text())["profiles"]
    profile = profiles[args.profile_number]
    bindings = {}
    for item in args.epoch_profile:
        epoch, index = map(int, item.split(":"))
        require(
            epoch > 0 and epoch not in bindings,
            "duplicate/invalid profile epoch binding",
        )
        bindings[epoch] = profiles[index]
    events = [json.loads(line) for line in args.trace.read_text().splitlines()]
    result = check(events, profile, bindings)
    result.update(
        status="passed",
        trace_sha256=hashlib.sha256(args.trace.read_bytes()).hexdigest(),
    )
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
