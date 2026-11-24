"""Independent opt-in polling overrides; preserve legacy cycle groups otherwise."""
from dataclasses import dataclass


@dataclass
class Job:
    index: int
    interval: float
    priority: int
    due: float
    failures: int = 0
    unsupported: bool = False


class Scheduler:
    def __init__(self, rules, bus_target=0.65):
        self.rules = rules
        self.bus_target = bus_target
        self.jobs = {}
        self.cost = 0.0
        self.utilization = 0.0
        self.next_slot = 0.0

    def configure(self, items, groups, now):
        self.jobs = {}
        for index, item in enumerate(items):
            name = item[1]
            rule = self.rules.get(name)
            if rule and groups[item[0]] > 0:
                self.jobs[name] = Job(index, rule["seconds"], rule.get("priority", 0), now)
        self.next_slot = now

    def reset(self, now):
        for job in self.jobs.values():
            job.due, job.failures, job.unsupported = now, 0, False

    def owns(self, name):
        return name in self.jobs

    def choose(self, now):
        if now < self.next_slot:
            return None
        ready = [(name, job) for name, job in self.jobs.items() if job.due <= now]
        if not ready:
            return None
        # Aging prevents starvation; priority gives a bounded head start.
        name, job = max(ready, key=lambda x: (now - x[1].due + x[1].priority * 0.1, -x[1].index))
        return name, job.index

    def complete(self, name, code, now, duration):
        job = self.jobs[name]
        self.cost = 0.9 * self.cost + 0.1 * max(0, duration)
        demand = sum(self.cost / j.interval for j in self.jobs.values() if not j.unsupported)
        self.utilization = min(1.0, demand)
        multiplier = max(1.0, demand / self.bus_target)
        if code == 1:
            job.failures, job.unsupported = 0, False
        else:
            job.failures = min(job.failures + 1, 10)
            # 0x03 is a device error, not proof of unsupported address.
            job.unsupported = code == 3 and job.failures >= 3
        interval = job.interval * multiplier
        if job.failures:
            interval = max(interval, min(900, interval * 2 ** min(job.failures, 6)))
        if job.unsupported:
            interval = max(interval, 900)
        job.due = now + interval
        self.next_slot = now + max(0, duration) * (1 / self.bus_target - 1)

    def snapshot(self):
        return {name: {"interval": j.interval, "priority": j.priority,
                       "failures": j.failures, "suspected_unsupported": j.unsupported}
                for name, j in self.jobs.items()}
