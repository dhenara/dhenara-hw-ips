"""Queue identity, age and bandwidth-policy reference contracts.

CK-rate legality is unconditional. service_div gates new selections only; an
accepted adjacent command pair always finishes on the following CK. Age units
are ticks of age_tick_div CK, saturating at 65535, independent of service_div.
"""

from dataclasses import dataclass, field


@dataclass
class Entry:
    tag: int
    generation: int
    write: bool
    port: int
    qos_class: int = 0
    age: int = 0
    picked: bool = False


@dataclass
class Queue:
    depth: int = 64
    age_tick_div: int = 8
    service_div: int = 2
    slots: dict[int, Entry] = field(default_factory=dict)
    generations: dict[int, int] = field(default_factory=dict)
    issued: set[int] = field(default_factory=set)
    batch_write: bool = False
    batch_count: int = 0

    def admit(self, index: int, tag: int, write: bool, port: int, qos_class: int = 0) -> None:
        if (
            qos_class not in range(4)
            or port not in (0, 1)
            or not 0 <= index < self.depth
            or index in self.slots
            or tag in self.issued
            or any(e.tag == tag for e in self.slots.values())
        ):
            raise ValueError("queue credit/duplicate")
        gen = self.generations.get(index, 0)
        if gen >= 65535:
            raise ValueError("generation wrap requires drained epoch reset")
        self.generations[index] = gen + 1
        self.slots[index] = Entry(tag, gen + 1, write, port, qos_class)

    def tick(self, ck: int) -> None:
        if self.age_tick_div < 1 or self.service_div not in (1, 2, 4):
            raise ValueError("scheduler configuration")
        if ck % self.age_tick_div == 0:
            for e in self.slots.values():
                e.age = min(65535, e.age + 1)

    def pick(self, ck: int, threshold: int = 32) -> tuple[int, int, int] | None:
        if ck % self.service_div or not self.slots:
            return None
        choices = [(i, e) for i, e in self.slots.items() if not e.picked]
        if not choices:
            return None
        # Empty-direction escape is unconditional, even below batch minimum.
        if not any(e.write == self.batch_write for _, e in choices):
            self.batch_write = not self.batch_write
            self.batch_count = 0
        urgent = [(i, e) for i, e in choices if e.age >= threshold]
        eligible = urgent or [(i, e) for i, e in choices if e.write == self.batch_write]
        i, e = min(eligible, key=lambda x: (0 if urgent else -x[1].qos_class, -x[1].age, x[1].tag))
        e.picked = True
        return i, e.generation, e.tag

    def issue(self, pick: tuple[int, int, int], *, legal: bool, owned: bool, credits: bool) -> bool:
        i, g, tag = pick
        e = self.slots.get(i)
        if e is None or (e.generation, e.tag) != (g, tag) or not e.picked or tag in self.issued:
            raise ValueError("duplicate/stale pick")
        if not legal or not owned or not credits:
            e.picked = False
            return False
        self.issued.add(tag)
        del self.slots[i]
        self.batch_count += 1
        return True


@dataclass
class Cap:
    window: int
    limit: tuple[int, int]
    mode: str = "strict"
    epoch: int = -1
    used: list[int] = field(default_factory=lambda: [0, 0])

    def admit(self, ck: int, direction: int, other_ready: bool, amount: int = 1) -> bool:
        if (
            amount < 1
            or self.window < 1
            or min(self.limit) < 0
            or direction not in (0, 1)
            or self.mode not in ("strict", "work_conserving")
        ):
            raise ValueError("cap configuration")
        epoch = ck // self.window
        if epoch != self.epoch:
            self.epoch = epoch
            self.used = [0, 0]
        if self.limit[direction] == 0:
            return False
        if self.mode == "work_conserving" and sum(self.used) + amount > sum(self.limit):
            return False
        if self.used[direction] + amount <= self.limit[direction] or (
            self.mode == "work_conserving" and not other_ready and sum(self.used) + amount <= sum(self.limit)
        ):
            self.used[direction] += amount
            return True
        return False


def check_fairness(
    grants: list[tuple[int, int]], eligible_since: dict[int, int], maximum_wait: int, *, observed_until: int
) -> None:
    # Eligibility is continuous over each supplied window. Maintenance, older
    # overlaps, strict caps and external stalls require separate windows.
    if maximum_wait < 1 or any(a[0] > b[0] for a, b in zip(grants, grants[1:], strict=False)):
        raise ValueError("fairness trace")
    for port, start in eligible_since.items():
        if observed_until < start:
            raise ValueError("fairness window")
        times = [start] + [ck for ck, p in grants if p == port and start <= ck <= observed_until] + [observed_until]
        if any(b - a > maximum_wait for a, b in zip(times, times[1:], strict=False)):
            raise ValueError("eligible port starvation")


def fairness_bound(depth: int, threshold: int, age_tick_div: int, maximum_issue_gap: int) -> int:
    """Oldest-first bound conditional on a bounded continuously legal issue gap."""
    if depth < 1 or threshold < 0 or age_tick_div < 1 or maximum_issue_gap < 1:
        raise ValueError("fairness configuration")
    return (threshold + 1) * age_tick_div + depth * maximum_issue_gap


@dataclass
class WeightedArbiter:
    weights: tuple[int, int] = (1, 1)
    direction: int = 0
    used: int = 0

    def choose(self, ready: tuple[bool, bool]) -> int | None:
        if any(type(w) is not int or not 0 <= w <= 65535 for w in self.weights):
            raise ValueError("weights")
        for _ in range(2):
            d = self.direction
            if self.weights[d] and ready[d] and self.used < self.weights[d]:
                self.used += 1
                if self.used == self.weights[d]:
                    self.direction = 1 - d
                    self.used = 0
                return d
            self.direction = 1 - d
            self.used = 0
        return None
