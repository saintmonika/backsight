"""Exploring many seeds, grouping failures into bugs, and saving them."""
from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .faults import FAULT_KINDS, Fault, Profile, get_profile
from .minimize import MinimizeResult, minimize
from .rules import Rule
from .runner import FAILED, PASSED, UNEXPLORED, UNTESTED, RunResult, run_one
from .world import World

STATE_DIR = ".backsight"


@dataclass
class Bug:
    rule: str
    seeds: list[int] = field(default_factory=list)
    representative: RunResult | None = None
    minimized: MinimizeResult | None = None


@dataclass
class Report:
    profile: Profile
    seeds: list[int]
    results: list[RunResult]
    bugs: list[Bug]

    @property
    def counts(self) -> Counter:
        return Counter(r.status for r in self.results)

    def fault_counts(self) -> Counter:
        c: Counter = Counter()
        for r in self.results:
            for f in r.faults:
                c[f.kind] += 1
        return c


def explore(
    scenario: Callable[[World], None],
    rules: Iterable[Rule],
    seeds: int | Iterable[int] = 1000,
    profile: str | Profile = "flaky_network",
    start: int = 1,
    max_failures: int | None = None,
    do_minimize: bool = True,
    save_dir: str | None = STATE_DIR,
) -> Report:
    rules = list(rules)
    profile = get_profile(profile)
    seed_list = list(range(start, start + seeds)) if isinstance(seeds, int) else list(seeds)

    results: list[RunResult] = []
    failures = 0
    for seed in seed_list:
        r = run_one(scenario, seed, rules, profile)
        results.append(r)
        if r.status == FAILED:
            failures += 1
            if max_failures is not None and failures >= max_failures:
                break

    bugs: dict[str, Bug] = {}
    for r in results:
        if r.status != FAILED:
            continue
        for rule_name in r.broken:
            bug = bugs.setdefault(rule_name, Bug(rule_name))
            bug.seeds.append(r.seed)
            rep = bug.representative
            if rep is None or (len(r.faults), r.seed) < (len(rep.faults), rep.seed):
                bug.representative = r

    ordered = sorted(bugs.values(), key=lambda b: (-len(b.seeds), b.rule))
    if do_minimize:
        for bug in ordered:
            rep = bug.representative
            if rep and rep.faults and bug.rule != "crash":
                bug.minimized = minimize(scenario, rules, rep.seed, bug.rule, rep.faults, profile)

    report = Report(profile, seed_list, results, ordered)
    if save_dir:
        save(report, save_dir)
    return report


# --- persistence -----------------------------------------------------------

def _run_path(save_dir: str, seed: int) -> str:
    return os.path.join(save_dir, "runs", f"{seed}.json")


def save(report: Report, save_dir: str = STATE_DIR) -> None:
    os.makedirs(os.path.join(save_dir, "runs"), exist_ok=True)
    minimal_by_seed = {b.minimized.seed: b.minimized for b in report.bugs if b.minimized}
    for r in report.results:
        if r.status != FAILED:
            continue
        m = minimal_by_seed.get(r.seed)
        data = {
            "seed": r.seed,
            "profile": r.profile,
            "broken": r.broken,
            "crash": r.crash,
            "faults": [f.to_json() for f in r.faults],
            "minimal_faults": [f.to_json() for f in m.minimal] if m else None,
            "trace_hash": r.trace_hash,
            "trace": r.trace.to_jsonable(),
        }
        with open(_run_path(save_dir, r.seed), "w") as fh:
            json.dump(data, fh, indent=2, default=repr)


def load_run(seed: int, save_dir: str = STATE_DIR) -> dict | None:
    path = _run_path(save_dir, seed)
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def saved_failing_seeds(save_dir: str = STATE_DIR) -> list[int]:
    runs = os.path.join(save_dir, "runs")
    if not os.path.isdir(runs):
        return []
    return sorted(int(name[:-5]) for name in os.listdir(runs) if name.endswith(".json"))


# --- terminal report -------------------------------------------------------

def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _explain(fault: Fault, run: RunResult | None) -> str:
    """Say what the agent saw when the fault hit, using the run's own trace."""
    if run is None:
        return ""
    for e in run.trace.seen_by_agent(fault.tool):
        if e.call == fault.call and e.detail:
            if e.detail.get("outcome") == "timeout" and e.detail.get("effect_applied"):
                return ": the agent saw a timeout, but the action had already gone through"
            if e.detail.get("outcome") == "timeout":
                return ": the agent saw a timeout and nothing happened"
            if e.detail.get("outcome") == "error":
                return ": the tool returned an error"
    return ""


def format_report(report: Report, target: str = "<scenario>") -> str:
    c = report.counts
    total = len(report.results)
    lines = [
        f"profile {report.profile.name} · seeds {report.seeds[0]}-{report.seeds[0] + total - 1}",
        "",
        f"{total} runs: {c[PASSED]} passed · {c[FAILED]} failed · {c[UNTESTED]} untested (no fault fired) · {c[UNEXPLORED]} unexplored",
    ]
    if c[UNEXPLORED]:
        first = next(r for r in report.results if r.status == UNEXPLORED)
        lines.append(f"unexplored means a run needed a response nobody recorded, e.g. seed {first.seed}: {first.unexplored}")
    fc = report.fault_counts()
    active = [k for k in FAULT_KINDS if report.profile.rates.get(k)]
    if active:
        parts = [f"{k} {fc[k]}" + (" (never fired)" if fc[k] == 0 else "") for k in active]
        lines.append("faults fired: " + " · ".join(parts))
    if not report.bugs:
        lines += ["", f"No rule broken in {total} seeds. That's evidence, not proof."]
        return "\n".join(lines)
    for i, bug in enumerate(report.bugs, 1):
        lines += ["", f"BUG {i}  {bug.rule}  ({len(bug.seeds)} seeds)"]
        rep = bug.representative
        m = bug.minimized
        if m:
            confirmed = f"confirmed {m.confirmed}/{m.confirm_runs} replays"
            if len(m.original) == len(m.minimal):
                cut = f"{_n(len(m.minimal), 'fault')}, already minimal"
            else:
                cut = f"{_n(len(m.original), 'fault')} cut to {len(m.minimal)}"
            lines.append(f"  minimized: seed {m.seed} · {cut} · {confirmed}")
            for f in m.minimal:
                lines.append(f"  cause:     {f.label()}{_explain(f, rep)}")
        elif rep:
            lines.append(f"  seed {rep.seed}: {rep.crash or 'no faults to minimize'}")
        seed = m.seed if m else rep.seed if rep else bug.seeds[0]
        lines.append(f"  replay:    backsight replay {target} {seed}" + (" --minimal" if m else ""))
        lines.append(f"  keep it:   backsight export {target} {seed}")
    return "\n".join(lines)
