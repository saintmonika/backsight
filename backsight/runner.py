"""Running one seed: build a world, run the scenario, check the rules."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from . import strict
from .cassettes import CassetteMiss
from .faults import CALM, Fault, FaultPlan, Profile, get_profile
from .rules import Rule, RuleBroken
from .trace import Trace
from .world import World

PASSED, FAILED, UNTESTED, UNEXPLORED = "passed", "failed", "untested", "unexplored"


@dataclass
class RunResult:
    seed: int
    profile: str
    # passed | failed | untested (passed, no fault fired) | unexplored (needed an unrecorded response)
    status: str
    broken: list[str] = field(default_factory=list)
    crash: str | None = None
    unexplored: str | None = None
    faults: list[Fault] = field(default_factory=list)
    trace: Trace = field(default_factory=Trace)
    pinned: bool = False

    @property
    def trace_hash(self) -> str:
        return self.trace.hash()

    def summary(self) -> str:
        if self.status != FAILED:
            return f"seed {self.seed}: {self.status}"
        why = ", ".join(self.broken)
        faults = "; ".join(f.label() for f in self.faults) or "no faults"
        return f"seed {self.seed}: FAILED {why} ({faults})"


def run_one(
    scenario: Callable[[World], None],
    seed: int,
    rules: Iterable[Rule],
    profile: str | Profile = "flaky_network",
    pinned: Iterable[Fault] | None = None,
    strict_mode: bool = True,
    allow_real: bool = False,
) -> RunResult:
    """Run a scenario once under one seed and return what happened."""
    profile = get_profile(profile)
    rules = list(rules)
    pinned_set = frozenset(pinned) if pinned is not None else None
    world = World(seed, profile, FaultPlan(seed, profile, pinned_set), allow_real=allow_real)

    step_rules = [r for r in rules if r.when == "step"]
    if step_rules:
        def check_steps(_event) -> None:
            for r in step_rules:
                if not r.check(world.trace):
                    raise RuleBroken(r)
        world.trace.on_add(check_steps)

    broken: list[str] = []
    crash: str | None = None
    with world, strict.guard(strict_mode):
        try:
            scenario(world)
        except RuleBroken as e:
            broken.append(e.rule.name)
        except strict.DeterminismLeak:
            raise
        except CassetteMiss:
            pass  # handled below: the run is unexplored, not failed
        except Exception as e:  # an uncaught exception in the agent is a failure
            crash = f"{type(e).__name__}: {e}"

    if world.cassette_miss is not None:
        # The run reached a call nobody recorded. It can't continue exactly,
        # so it proves nothing either way. Never count it as passed.
        return RunResult(
            seed=seed, profile=profile.name, status=UNEXPLORED,
            unexplored=str(world.cassette_miss), faults=list(world.faults_fired),
            trace=world.trace, pinned=pinned_set is not None,
        )

    if crash:
        broken.append("crash")
    for r in rules:
        if r.when == "end" and r.name not in broken:
            try:
                ok = r.check(world.trace)
            except Exception:
                ok = False
            if not ok:
                broken.append(r.name)

    if broken:
        status = FAILED
    elif not world.faults_fired:
        status = UNTESTED
    else:
        status = PASSED
    return RunResult(
        seed=seed,
        profile=profile.name,
        status=status,
        broken=broken,
        crash=crash,
        faults=list(world.faults_fired),
        trace=world.trace,
        pinned=pinned_set is not None,
    )


@dataclass
class RecordResult:
    saved: list[str]          # cassette files written
    calls: dict[str, int]     # responses recorded per tool
    error: str | None = None


def record(scenario: Callable[[World], None], seed: int = 1) -> RecordResult:
    """Run the scenario once with no faults, calling the real functions of
    cassette tools and saving their responses. Fake (sim=) tools stay fake."""
    world = World(seed, CALM, FaultPlan(seed, CALM, frozenset()), record=True)
    error: str | None = None
    with world:  # strict mode off: the real API is supposed to be reached here
        try:
            scenario(world)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
    saved = world.save_cassettes()
    calls: dict[str, int] = {}
    for e in world.trace.events:
        if e.kind == "seen" and e.tool and any(c.tool == e.tool for c in world._cassettes.values()):
            calls[e.tool] = calls.get(e.tool, 0) + 1
    return RecordResult([c.path for c in saved], calls, error)
