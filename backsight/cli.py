"""Command line: explore, replay, minimize, verify, export, doctor."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from .explore import explore, format_report, load_run, saved_failing_seeds
from .export import export_test
from .faults import Fault, get_profile
from .loader import load_scenario
from .minimize import minimize
from .runner import FAILED, record, run_one


def _profile(args, module) -> str:
    if getattr(args, "profile", None):
        return get_profile(args.profile).name
    return get_profile(getattr(module, "PROFILE", "flaky_network")).name


def cmd_explore(args) -> int:
    m = load_scenario(args.scenario)
    profile = _profile(args, m)
    print(f"backsight explore {args.scenario}")
    report = explore(m.scenario, m.RULES, seeds=args.seeds, start=args.start, profile=profile,
                     max_failures=args.max_failures, do_minimize=not args.no_minimize)
    print(format_report(report, args.scenario))
    return 1 if report.bugs else 0


def cmd_replay(args) -> int:
    m = load_scenario(args.scenario)
    saved = load_run(args.seed)
    profile = _profile(args, m) if args.profile or not saved else saved["profile"]
    pinned = None
    if args.minimal:
        if not saved or not saved.get("minimal_faults"):
            print(f"No minimized plan saved for seed {args.seed}. Run `backsight minimize` first.")
            return 2
        pinned = [Fault.from_json(f) for f in saved["minimal_faults"]]
    r = run_one(m.scenario, args.seed, m.RULES, profile, pinned=pinned)

    match = ""
    if saved and pinned is None:
        same = saved["trace_hash"] == r.trace_hash
        match = " (matches saved run)" if same else f" (DIFFERS from saved run {saved['trace_hash']}: the code changed)"
    plan = "minimal fault plan" if pinned is not None else "seeded fault plan"
    print(f"seed {args.seed} · {profile} · {plan} · trace {r.trace_hash}{match}")
    print()
    for line in r.trace.timeline():
        print(line)
    print()
    if r.status == FAILED:
        for name in r.broken:
            print(f"RULE BROKEN: {name}" + (f" ({r.crash})" if name == "crash" else ""))
    else:
        print(f"Result: {r.status}")
    return 1 if r.status == FAILED else 0


def cmd_minimize(args) -> int:
    m = load_scenario(args.scenario)
    saved = load_run(args.seed)
    profile = _profile(args, m) if args.profile or not saved else saved["profile"]
    r = run_one(m.scenario, args.seed, m.RULES, profile)
    if r.status != FAILED:
        print(f"seed {args.seed} doesn't fail right now ({r.status}). Nothing to minimize.")
        return 0
    rule = args.rule or r.broken[0]
    res = minimize(m.scenario, m.RULES, args.seed, rule, r.faults, profile)
    print(f"seed {args.seed} · rule {rule} · {len(res.original)} fault(s) cut to {len(res.minimal)} "
          f"in {res.attempts} reruns · confirmed {res.confirmed}/{res.confirm_runs}")
    for f in res.minimal:
        print(f"  cause: {f.label()}")
    print("Locally minimal: no single remaining fault can be removed and still break the rule.")
    return 0


def cmd_verify(args) -> int:
    m = load_scenario(args.scenario)
    profile = _profile(args, m)
    old = saved_failing_seeds()
    still = [s for s in old if run_one(m.scenario, s, m.RULES, profile).status == FAILED]
    fresh_failures = []
    for seed in range(args.fresh_start, args.fresh_start + args.fresh):
        if run_one(m.scenario, seed, m.RULES, profile).status == FAILED:
            fresh_failures.append(seed)
    if old:
        print(f"{len(old) - len(still)} of {len(old)} saved failing seeds now pass.")
    else:
        print("No saved failing seeds yet. Run `backsight explore` first.")
    if fresh_failures:
        print(f"{len(fresh_failures)} new failures in {args.fresh} fresh seeds (first: {fresh_failures[0]}).")
    else:
        print(f"No new failures in {args.fresh} fresh seeds.")
    if still:
        print(f"Still failing: {', '.join(map(str, still[:10]))}{' ...' if len(still) > 10 else ''}")
    return 1 if still or fresh_failures else 0


def cmd_export(args) -> int:
    m = load_scenario(args.scenario)
    saved = load_run(args.seed)
    profile = _profile(args, m) if args.profile or not saved else saved["profile"]
    if saved and saved.get("minimal_faults"):
        faults = [Fault.from_json(f) for f in saved["minimal_faults"]]
        rule = args.rule or saved["broken"][0]
    else:
        r = run_one(m.scenario, args.seed, m.RULES, profile)
        if r.status != FAILED:
            print(f"seed {args.seed} doesn't fail, so there's nothing to export.")
            return 2
        rule = args.rule or r.broken[0]
        faults = r.faults
    path = export_test(args.scenario, args.seed, rule, faults, profile, args.out)
    print(f"Wrote {path}. It fails while the bug exists and passes once it's fixed.")
    return 0


def cmd_doctor(args) -> int:
    m = load_scenario(args.scenario)
    profile = _profile(args, m)
    hashes = [run_one(m.scenario, args.seed, m.RULES, profile).trace_hash for _ in range(3)]
    out = subprocess.run(
        [sys.executable, "-m", "backsight", "_hash", args.scenario, str(args.seed), profile],
        capture_output=True, text=True,
    )
    other = out.stdout.strip() if out.returncode == 0 else f"error: {out.stderr.strip()[-300:]}"
    hashes.append(other)
    if len(set(hashes)) == 1:
        print(f"OK: seed {args.seed} produced trace {hashes[0]} in 3 runs here and 1 in a separate process.")
        return 0
    print("NOT REPRODUCIBLE: the same seed produced different traces:")
    for label, h in zip(["run 1", "run 2", "run 3", "separate process"], hashes):
        print(f"  {label}: {h}")
    print("Something in the agent reads real time, randomness, or state outside the world.")
    return 1


def cmd_record(args) -> int:
    m = load_scenario(args.scenario)
    res = record(m.scenario, seed=args.seed)
    if res.error:
        print(f"The run hit an error while recording: {res.error}")
    if not res.saved:
        print("Nothing new to record. Tools need cassette= to be recorded.")
        return 1 if res.error else 0
    for path in res.saved:
        print(f"Saved {os.path.relpath(path)}")
    total = ", ".join(f"{tool}: {n}" for tool, n in sorted(res.calls.items()))
    print(f"Recorded responses ({total}). Runs now replay these instead of calling the real API.")
    return 1 if res.error else 0


def cmd_hash(args) -> int:
    m = load_scenario(args.scenario)
    print(run_one(m.scenario, args.seed, m.RULES, args.profile).trace_hash)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="backsight", description="Find and replay AI agent failures.")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    e = sub.add_parser("explore", help="run many seeds and group failures into bugs")
    e.add_argument("scenario")
    e.add_argument("--seeds", type=int, default=1000)
    e.add_argument("--start", type=int, default=1)
    e.add_argument("--profile")
    e.add_argument("--max-failures", type=int)
    e.add_argument("--no-minimize", action="store_true")
    e.set_defaults(fn=cmd_explore)

    r = sub.add_parser("replay", help="rerun one seed exactly and print its timeline")
    r.add_argument("scenario")
    r.add_argument("seed", type=int)
    r.add_argument("--minimal", action="store_true", help="replay only the minimized faults")
    r.add_argument("--profile")
    r.set_defaults(fn=cmd_replay)

    mz = sub.add_parser("minimize", help="cut a failing fault plan down to the faults that matter")
    mz.add_argument("scenario")
    mz.add_argument("seed", type=int)
    mz.add_argument("--rule")
    mz.add_argument("--profile")
    mz.set_defaults(fn=cmd_minimize)

    v = sub.add_parser("verify", help="rerun saved failing seeds plus fresh ones after a fix")
    v.add_argument("scenario")
    v.add_argument("--fresh", type=int, default=1000)
    v.add_argument("--fresh-start", type=int, default=100_001)
    v.add_argument("--profile")
    v.set_defaults(fn=cmd_verify)

    x = sub.add_parser("export", help="write a pytest regression test for a failing seed")
    x.add_argument("scenario")
    x.add_argument("seed", type=int)
    x.add_argument("--rule")
    x.add_argument("--out", default="tests")
    x.add_argument("--profile")
    x.set_defaults(fn=cmd_export)

    d = sub.add_parser("doctor", help="check that runs are actually reproducible")
    d.add_argument("scenario")
    d.add_argument("--seed", type=int, default=1)
    d.add_argument("--profile")
    d.set_defaults(fn=cmd_doctor)

    rc = sub.add_parser("record", help="call the real API once and save responses for cassette tools")
    rc.add_argument("scenario")
    rc.add_argument("--seed", type=int, default=1)
    rc.set_defaults(fn=cmd_record)

    h = sub.add_parser("_hash")
    h.add_argument("scenario")
    h.add_argument("seed", type=int)
    h.add_argument("profile")
    h.set_defaults(fn=cmd_hash)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
