"""Turn a failure into a pytest regression test."""
from __future__ import annotations

import os
import re

from .faults import Fault

TEMPLATE = '''"""Regression test written by `backsight export`.

Seed {seed} broke rule `{rule}` with: {labels}.
It replays that exact fault plan and fails while the bug exists.
"""
import os

from backsight import Fault, load_scenario, run_one

SCENARIO = load_scenario(os.path.join(os.path.dirname(__file__), {scenario_rel!r}))
FAULTS = [{faults}]


def test_seed_{seed}_{rule_slug}():
    result = run_one(SCENARIO.scenario, seed={seed}, rules=SCENARIO.RULES,
                     profile={profile!r}, pinned=FAULTS)
    assert {rule!r} not in result.broken, "\\n".join([result.summary(), *result.trace.timeline()])
'''


def export_test(scenario_path: str, seed: int, rule: str, faults: list[Fault], profile: str, out_dir: str = "tests") -> str:
    os.makedirs(out_dir, exist_ok=True)
    rule_slug = re.sub(r"\W+", "_", rule).strip("_") or "rule"
    path = os.path.join(out_dir, f"test_backsight_seed{seed}_{rule_slug}.py")
    scenario_rel = os.path.relpath(os.path.abspath(scenario_path), os.path.abspath(out_dir))
    body = TEMPLATE.format(
        seed=seed,
        rule=rule,
        rule_slug=rule_slug,
        labels="; ".join(f.label() for f in faults) or "no faults",
        scenario_rel=scenario_rel,
        faults=", ".join(f"Fault({f.tool!r}, {f.call}, {f.kind!r})" for f in faults),
        profile=profile,
    )
    with open(path, "w") as fh:
        fh.write(body)
    return path
