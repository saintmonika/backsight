# Backsight

**Test how AI agents behave when their tools fail, and replay any failure exactly from one seed.**

An agent's logs show what it was *told* happened, not what *actually* happened. A payment API can process a refund and still time out. The agent retries, and the customer gets refunded twice. It only happens when the timing lines up wrong, so it passes testing and shows up in production.

Backsight runs your agent against simulated tools, injects failures from a seed, checks rules against what really happened, and gives you a seed that reproduces each failure exactly.

```
explore  ->  detect  ->  minimize  ->  replay  ->  verify and keep
```

Pure Python, standard library only. Status: early prototype (v0.1.0.dev0).

## Try the demo

```bash
git clone <this repo> && cd backsight
python -m backsight explore examples/refund_agent/scenario.py
```

Real output:

```
profile flaky_network · seeds 1-1000

1000 runs: 201 passed · 243 failed · 556 untested (no fault fired) · 0 unexplored
faults fired: timeout_before_effect 192 · timeout_after_effect 280 · latency_spike 169

BUG 1  one_confirmation_email  (135 seeds)
  minimized: seed 4 · 1 fault, already minimal · confirmed 5/5 replays
  cause:     latency_spike on send_email call #1: the agent saw a timeout, but the action had already gone through

BUG 2  never_refund_twice  (124 seeds)
  minimized: seed 23 · 1 fault, already minimal · confirmed 5/5 replays
  cause:     latency_spike on refund call #1: the agent saw a timeout, but the action had already gone through
```

Replay the exact failure:

```
$ python -m backsight replay examples/refund_agent/scenario.py 23
seed 23 · flaky_network · seeded fault plan · trace b326391544333be6 (matches saved run)

   0.000s  agent -> lookup_order(order_id='1042')
   0.311s  agent <- lookup_order: ok
   0.311s  agent -> refund(order_id='1042', amount=40.0)
   0.311s  world    refund applied (order_id='1042', amount=40.0, refund_id='re_1')
   2.311s  agent <- refund: TIMEOUT  (it actually went through)  [fault: latency_spike]
   2.311s  agent    waits 0.5s
   2.811s  agent -> refund(order_id='1042', amount=40.0)
   2.811s  world    refund applied (order_id='1042', amount=40.0, refund_id='re_2')
   ...
RULE BROKEN: never_refund_twice
```

Cut a messy failure down to what matters:

```
$ python -m backsight minimize examples/refund_agent/scenario.py 21 --rule never_refund_twice
seed 21 · rule never_refund_twice · 3 fault(s) cut to 1 in 4 reruns · confirmed 5/5
  cause: latency_spike on refund call #1
```

Keep it as a regression test, fix it, and check the fix:

```bash
python -m backsight export examples/refund_agent/scenario.py 23   # writes a pytest file
# set FIXED = True in scenario.py (adds idempotency keys)
python -m backsight verify examples/refund_agent/scenario.py
# 243 of 243 saved failing seeds now pass.
# No new failures in 1000 fresh seeds.
```

## Use it on your agent

A scenario file defines tools, rules, and one task:

```python
import backsight
from backsight import fakes, rule

@backsight.tool(timeout_s=2.0, sim=fakes.Payments)   # in a run, calls hit the fake
def refund(order_id: str, amount: float, idempotency_key=None):
    return stripe_client.refund(order_id, amount)      # only runs outside Backsight

@rule
def never_refund_twice(trace):
    """No order is refunded more than once."""
    ids = [e.args["order_id"] for e in trace.effects("refund")]
    return len(ids) == len(set(ids))

RULES = [never_refund_twice]

def scenario(world):
    agent = MyAgent(tools=[refund], sleep=world.sleep)
    agent.handle("Order 1042 arrived broken, I want my money back")
```

- `trace.effects(kind)` is what really happened. `trace.seen_by_agent(tool)` is what the agent was told. Rules check effects.
- Tools without a `sim=` fake refuse to run during a test unless you pass `allow_real=True`.
- Use `world.now()`, `world.sleep()`, `world.random("name")`, and `world.uuid()` instead of real time and randomness. **Strict mode** catches leaks and names the line and the fix: `agent.py:42 calls time.time(). Use world.now() so runs replay exactly.`

## Commands

| Command | What it does |
| --- | --- |
| `explore <scenario>` | Runs many seeds, groups failures into bugs, minimizes each, saves runs to `.backsight/` |
| `replay <scenario> <seed>` | Reruns one seed exactly, checks the trace hash, prints the timeline (`--minimal` for the minimized plan) |
| `minimize <scenario> <seed>` | Cuts a failing fault plan down to the faults that matter |
| `verify <scenario>` | Reruns saved failing seeds plus fresh ones after a fix |
| `export <scenario> <seed>` | Writes a pytest regression test for that failure |
| `doctor <scenario>` | Checks the same seed gives the same trace, including in a separate process |

## What it promises, exactly

- **Same code, same fixtures, same seed: same trace.** Only for what flows through Backsight. `doctor` checks this.
- **Minimized means locally minimal:** no single remaining fault can be removed and still break the rule. Not necessarily the smallest possible set.
- **`verify` gives evidence, not proof:** "243 of 243 failing seeds pass, no new failures in 1000 fresh seeds." Backsight never says "bug-free."
- **Every run gets a result:** passed, failed, untested (no fault fired), or unexplored (reached a model state that was never recorded; applies once model replay ships).

## Not built yet

Recording real tool responses as cassettes (`backsight record`), record and replay for live model calls, parallel exploration, framework adapters, and MCP wrapping. Strict mode doesn't catch `datetime.now()` yet.

## Tests

```bash
python -m pytest -q
```

Includes a hollowness check: disable timeout-after-effect inside the library and three tests fail.
