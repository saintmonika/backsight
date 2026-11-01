"""Minimizing: cut a failing fault plan down to the faults that matter.

The result is locally minimal: no single remaining fault can be removed
without the failure going away. It is not guaranteed to be the smallest
possible set.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from .faults import Fault, Profile
from .rules import Rule
from .runner import FAILED, run_one
from .world import World


@dataclass
class MinimizeResult:
    seed: int
    rule: str
    original: list[Fault]
    minimal: list[Fault]
    attempts: int
    confirmed: int = 0
    confirm_runs: int = 0
    hashes: set[str] = field(default_factory=set)

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed == self.confirm_runs and len(self.hashes) == 1


def minimize(
    scenario: Callable[[World], None],
    rules: Iterable[Rule],
    seed: int,
    rule: str,
    faults: Iterable[Fault],
    profile: str | Profile,
    confirm_runs: int = 5,
) -> MinimizeResult:
    rules = list(rules)
    current = sorted(set(faults))
    original = list(current)
    attempts = 0

    def still_fails(plan: list[Fault]) -> bool:
        nonlocal attempts
        attempts += 1
        r = run_one(scenario, seed, rules, profile, pinned=plan)
        return r.status == FAILED and rule in r.broken

    changed = True
    while changed and len(current) > 1:
        changed = False
        for f in list(current):
            trial = [x for x in current if x != f]
            if still_fails(trial):
                current = trial
                changed = True
                break

    result = MinimizeResult(seed, rule, original, current, attempts, confirm_runs=confirm_runs)
    for _ in range(confirm_runs):
        r = run_one(scenario, seed, rules, profile, pinned=current)
        result.hashes.add(r.trace_hash)
        if r.status == FAILED and rule in r.broken:
            result.confirmed += 1
    return result
