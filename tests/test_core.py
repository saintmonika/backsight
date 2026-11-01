"""Backsight's own tests. A reproducibility tool that isn't reproducible is worse than none."""
import os
import subprocess
import sys
import time

import pytest

import backsight
from backsight import Fault, fakes, rule, run_one
from backsight.faults import LATENCY_SPIKE, TIMEOUT_AFTER, TIMEOUT_BEFORE, Profile
from backsight.minimize import minimize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_PATH = os.path.join(ROOT, "examples", "refund_agent", "scenario.py")


@pytest.fixture
def demo(monkeypatch):
    module = backsight.load_scenario(DEMO_PATH)
    monkeypatch.setattr(module, "FIXED", False)
    return module


# --- determinism -------------------------------------------------------------

def test_same_seed_gives_identical_trace(demo):
    for seed in range(1, 201):
        a = run_one(demo.scenario, seed, demo.RULES, "flaky_network")
        b = run_one(demo.scenario, seed, demo.RULES, "flaky_network")
        assert a.trace_hash == b.trace_hash, f"seed {seed} drifted"


def test_same_seed_matches_across_processes(demo):
    for seed in (3, 23, 517):
        here = run_one(demo.scenario, seed, demo.RULES, "flaky_network").trace_hash
        out = subprocess.run(
            [sys.executable, "-m", "backsight", "_hash", DEMO_PATH, str(seed), "flaky_network"],
            capture_output=True, text=True, cwd=ROOT, check=True,
        )
        assert out.stdout.strip() == here


def test_different_seeds_explore_different_runs(demo):
    hashes = {run_one(demo.scenario, s, demo.RULES, "flaky_network").trace_hash for s in range(1, 101)}
    assert len(hashes) > 10


def test_named_streams_are_independent():
    plan = backsight.faults.FaultPlan(7, backsight.CALM)
    w1 = backsight.World(7, backsight.CALM, plan)
    w2 = backsight.World(7, backsight.CALM, plan)
    w2.random("other").random()  # using another stream must not shift this one
    assert [w1.random("a").random() for _ in range(5)] == [w2.random("a").random() for _ in range(5)]


# --- strict mode -------------------------------------------------------------

def test_strict_mode_names_the_line_and_the_fix():
    def leaky(world):
        time.time()  # the leak

    with pytest.raises(backsight.DeterminismLeak) as err:
        run_one(leaky, 1, [], "calm")
    message = str(err.value)
    assert "test_core.py:" in message and "world.now()" in message


def test_strict_mode_restores_real_functions():
    with pytest.raises(backsight.DeterminismLeak):
        run_one(lambda w: time.sleep(1), 1, [], "calm")
    assert time.time() > 0  # real time works again outside the run


# --- faults do what they say -------------------------------------------------

@backsight.tool(timeout_s=2.0, sim=fakes.Payments)
def refund(order_id: str, amount: float, idempotency_key=None):
    raise NotImplementedError


def _call_refund_once(world):
    try:
        refund(order_id="1", amount=5.0)
    except TimeoutError:
        world.reply("timed out")


def test_timeout_after_effect_leaves_the_action_applied():
    r = run_one(_call_refund_once, 1, [], "calm", pinned=[Fault("refund", 0, TIMEOUT_AFTER)])
    assert len(r.trace.effects("refund")) == 1
    seen = r.trace.seen_by_agent("refund")[0].detail
    assert seen["outcome"] == "timeout" and seen["effect_applied"] is True


def test_timeout_before_effect_applies_nothing():
    r = run_one(_call_refund_once, 1, [], "calm", pinned=[Fault("refund", 0, TIMEOUT_BEFORE)])
    assert r.trace.effects("refund") == []
    assert r.trace.seen_by_agent("refund")[0].detail["effect_applied"] is False


def test_tools_without_a_fake_never_reach_the_real_system():
    @backsight.tool
    def charge_card(amount: float):
        raise AssertionError("the real function must not run")

    r = run_one(lambda w: charge_card(amount=1.0), 1, [], "calm")
    assert r.status == "failed" and "RealCallBlocked" in r.crash


def test_step_rules_stop_the_run_at_the_first_break():
    @rule(when="step")
    def at_most_one_refund(trace):
        return len(trace.effects("refund")) <= 1

    def refund_three_times(world):
        for _ in range(3):
            refund(order_id="1", amount=5.0)

    r = run_one(refund_three_times, 1, [at_most_one_refund], "calm")
    assert r.broken == ["at_most_one_refund"]
    assert len(r.trace.effects("refund")) == 2  # stopped right at the second refund


# --- the demo: known bug found, fix holds ------------------------------------

def test_demo_bug_is_found(demo):
    report = backsight.explore(demo.scenario, demo.RULES, seeds=500, save_dir=None)
    assert "never_refund_twice" in [b.rule for b in report.bugs]


def test_demo_fix_passes(demo, monkeypatch):
    monkeypatch.setattr(demo, "FIXED", True)
    report = backsight.explore(demo.scenario, demo.RULES, seeds=2000, save_dir=None)
    assert report.bugs == []


def test_the_tests_are_real(demo):
    """Remove the faults that cause the bug and the bug must disappear.
    If it didn't, the detection would be hollow."""
    no_late_timeouts = Profile("no_late_timeouts", rates={TIMEOUT_BEFORE: 0.2})
    report = backsight.explore(demo.scenario, demo.RULES, seeds=500, profile=no_late_timeouts, save_dir=None)
    assert "never_refund_twice" not in [b.rule for b in report.bugs]


# --- minimizing --------------------------------------------------------------

def test_minimizer_cuts_a_planted_failure_to_the_one_fault_that_matters(demo):
    planted = [
        Fault("lookup_order", 0, TIMEOUT_BEFORE),   # harmless: retried, no effect
        Fault("refund", 0, TIMEOUT_AFTER),          # the real cause
        Fault("send_email", 0, LATENCY_SPIKE),      # unrelated
    ]
    first = run_one(demo.scenario, 1, demo.RULES, "flaky_network", pinned=planted)
    assert "never_refund_twice" in first.broken
    res = minimize(demo.scenario, demo.RULES, 1, "never_refund_twice", planted, "flaky_network")
    assert res.minimal == [Fault("refund", 0, TIMEOUT_AFTER)]
    assert res.is_confirmed
