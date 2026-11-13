"""Observed registered-reset consumption, shared by Run and the timed RTL test.

Each falling observation retains its actual next pre-NBA rising input. A reset
asserted by lifecycle NBA must be consumed at that rising edge; configuration
must be invalid at the immediately following falling observation. Time alone
never proves consumption. These records cannot be reconstructed for old traces.
"""

from system.host import require

RECOVERY_PHASE = "registered_reset_next_rising_then_falling_v1"
RECOVERY_FIELDS = (
    "destructive boot config config_valid prepared_valid trained trusted train_request "
    "enabled admission issue confirmed reset_request reset_authorized recover_command"
).split()
RECOVERY_REGISTERED = ("destructive boot config config_valid prepared_valid trained trusted train_request").split()


class RecoveryConsumption:
    def __init__(self, channels=2, ck_ps=1250):
        self.channels = channels
        self.ck_ps = ck_ps
        self.last = [None] * channels
        self.edges = [None] * channels
        self.pending = [None] * channels
        self.commands = [None] * channels
        self.low = [False] * channels
        self.high = [False] * channels
        self.completed = set()
        self.history = []
        self.retired = [None] * channels
        self.active = False

    def begin(self):
        require(not any(self.pending) and not any(self.commands), "unfinished reset at case boundary")
        self.low = [False] * self.channels
        self.high = [False] * self.channels
        self.completed.clear()
        self.active = False

    def safe(self, e):
        require(
            e["state"] == 0
            and e["owner"] == 1
            and not any(e[k] for k in ("enabled", "admission", "issue", "trusted", "trained", "train_request")),
            "recovery before immediate disabled PHY/BOOT ownership",
        )

    def confirmation(self, e):
        c = e["channel"]
        if not e["confirmed"]:
            self.high[c] = False
            if e["reset_request"]:
                self.low[c] = True
        elif self.low[c]:
            self.high[c] = True

    def service(self, e):
        c, old = e["channel"], self.last[e["channel"]]
        if self.active and len(self.completed) != self.channels:
            # Existing active configuration can remain visible only until its
            # own consumed reset; a new publication/training cannot cross the
            # all-channel invalidation fence.
            require(
                not e["enabled"]
                and not e["admission"]
                and not e["issue"]
                and not e["trusted"]
                and not e["train_request"]
                and not (old and not old["trained"] and e["trained"])
                and not (old and not old["config_valid"] and e["config_valid"])
                and not (old and not old["prepared_valid"] and e["prepared_valid"])
                and not (old and e["config"] != old["config"]),
                "publication/training/service before both reset consumptions",
            )
        if self.retired[c] is not None and self.pending[c] is None and e["config_valid"]:
            require(e["config"] > self.retired[c], "reused retired recovery configuration")
        if e["trusted"]:
            require(
                e["trained"] and e["config_valid"] and e["training_epoch"] == e["boot"],
                "stale trusted epoch",
            )
        if self.retired[c] is not None and self.pending[c] is None:
            if e["train_request"] or e["trained"]:
                require(
                    e["config_valid"] and e["training_epoch"] == e["boot"], "training before fresh recovery profile"
                )
            if e["enabled"] or e["admission"] or e["issue"]:
                require(e["trusted"] and e["trained"] and e["config_valid"], "service before fresh recovery training")

    def sample(self, s, source_settled, failed_case):
        c = s["channel"]
        require(0 <= c < self.channels and all(k in s for k in RECOVERY_FIELDS), "incomplete recovery sample")
        old, edge, pending = self.last[c], self.edges[c], self.pending[c]
        require(0 <= s["boot"] <= 0xFFFFFFFF and 0 <= s["config"] <= 0xFFFFFFFF, "recovery epoch overflow")
        if old is not None:
            require(
                edge is not None
                and s["tick"] == old["tick"] + 1 == edge["tick"] + 1
                and s["ps"] == old["ps"] + self.ck_ps == edge["ps"] + self.ck_ps // 2,
                "missing/stale recovery consumption phase",
            )
        self.confirmation(s)
        transition = s["destructive"] and not (old and old["destructive"])
        if transition:
            require(
                failed_case and source_settled and self.low[c] and self.high[c] and s["confirmed"],
                "channel destructive recovery lacks fresh confirmation/source settlement",
            )
            require(
                old is not None
                and edge is not None
                and self.commands[c] == edge
                and edge["recover_command"]
                and edge["reset_authorized"]
                and edge["confirmed"]
                and edge["state"] == 5
                and 0 <= old["boot"] < 0xFFFFFFFF
                and s["boot"] == old["boot"] + 1,
                "boot/pulse mismatch or unconsumed recovery command",
            )
            require(pending is None and c not in self.completed, "duplicate reset in recovery interval")
            self.safe(s)
            require(s["config"] == old["config"], "reset changed configuration identity")
            self.commands[c] = None
            self.active = True
            self.retired[c] = old["config"]
            self.pending[c] = dict(assertion=dict(s), consumption=None, invalid_seen=not s["config_valid"])
            self.low[c] = self.high[c] = False
        elif pending is not None:
            require(pending["consumption"] is not None, "missing registered reset consumption witness")
            a, w = pending["assertion"], pending["consumption"]
            require(
                s["tick"] == a["tick"] + 1
                and s["ps"] == w["ps"] + self.ck_ps // 2
                and s["boot"] == a["boot"]
                and s["config"] == a["config"],
                "wrong reset invalidation identity",
            )
            self.safe(s)
            require(not s["destructive"], "stretched registered reset pulse")
            require(not s["config_valid"] and not s["prepared_valid"], "late/stuck recovery configuration invalidation")
            self.history.append(
                dict(channel=c, boot=s["boot"], config=s["config"], assertion=a, consumption=w, invalidation=dict(s))
            )
            self.pending[c] = None
            self.completed.add(c)
        elif old is not None:
            require(self.commands[c] is None, "recovery command missing boot/reset result")
            require(s["boot"] == old["boot"], "boot change without destructive reset")
            require(not s["destructive"], "stretched registered reset pulse")
        self.service(s)
        self.edges[c] = None
        self.last[c] = s
        return bool(transition)

    def consume(self, e, source_settled):
        c = e["channel"]
        require(0 <= c < self.channels and all(k in e for k in RECOVERY_FIELDS), "incomplete recovery witness")
        s = self.last[c]
        require(
            s is not None and self.edges[c] is None and e["tick"] == s["tick"] and e["ps"] == s["ps"] + self.ck_ps // 2,
            "wrong/duplicate/reordered recovery witness",
        )
        require(all(e[k] == s[k] for k in RECOVERY_REGISTERED), "recovery registered phase binding")
        self.confirmation(e)
        pending = self.pending[c]
        if pending is not None:
            require(pending["consumption"] is None and e["destructive"], "missing/duplicate reset consumption")
            require(source_settled, "reset consumption before source settlement")
            self.safe(e)
            require(not pending["invalid_seen"] or not e["config_valid"], "recovery invalidation resurrected")
            pending["consumption"] = dict(e)
        elif e["recover_command"]:
            require(
                source_settled
                and self.low[c]
                and self.high[c]
                and e["confirmed"]
                and e["reset_authorized"]
                and e["state"] == 5
                and e["boot"] < 0xFFFFFFFF,
                "unconfirmed/unsettled recovery command",
            )
            require(self.commands[c] is None, "duplicate recovery command")
            self.commands[c] = dict(e)
        self.service(e)
        self.edges[c] = e

    def require_complete(self):
        require(
            self.completed == set(range(self.channels))
            and not any(self.pending)
            and all(
                s is not None and not s["destructive"] and not s["config_valid"] and not s["prepared_valid"]
                for s in self.last
            ),
            "baseline before both completed configuration invalidations",
        )

    def finish(self):
        require(
            not any(self.pending) and not any(self.commands) and all(not s or not s["destructive"] for s in self.last),
            "unfinished reset tail",
        )
        return self.history
