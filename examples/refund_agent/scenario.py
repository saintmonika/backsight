"""Backsight scenario: a support agent handles a refund request.

Try it:
    python3 -m backsight explore examples/refund_agent/scenario.py
    python3 -m backsight replay  examples/refund_agent/scenario.py <seed> --minimal

Then set FIXED = True (adds idempotency keys) and run:
    python3 -m backsight verify  examples/refund_agent/scenario.py

lookup_order replays real responses from cassettes/lookup_order.json.
To capture them again from the "real API" (orders_api.json here), run:
    python3 -m backsight record  examples/refund_agent/scenario.py
"""
from __future__ import annotations

import json
import os

import backsight
from backsight import fakes, rule

from agent import SupportAgent

FIXED = False
PROFILE = "flaky_network"
HERE = os.path.dirname(os.path.abspath(__file__))


# --- tools ----------------------------------------------------------------
# lookup_order: real responses, recorded once with `backsight record`.
# refund and send_email change things, so they run against fakes that track
# real effects. Their real bodies never run inside Backsight.

@backsight.tool(timeout_s=2.0, cassette="cassettes/lookup_order.json")
def lookup_order(order_id: str) -> dict:
    # Stand-in for a real order API. Only called by `backsight record`.
    with open(os.path.join(HERE, "orders_api.json")) as fh:
        return json.load(fh)[order_id]


@backsight.tool(timeout_s=2.0, sim=fakes.Payments)
def refund(order_id: str, amount: float, idempotency_key: str | None = None) -> dict:
    raise NotImplementedError("your real payments API (e.g. Stripe) goes here")


@backsight.tool(timeout_s=2.0, sim=fakes.Email)
def send_email(to: str, subject: str, body: str, idempotency_key: str | None = None) -> dict:
    raise NotImplementedError("your real email API goes here")


# --- rules: checked against what actually happened -------------------------

@rule
def never_refund_twice(trace):
    """No order is refunded more than once."""
    ids = [e.args["order_id"] for e in trace.effects("refund")]
    return len(ids) == len(set(ids))


@rule
def one_confirmation_email(trace):
    """The customer gets at most one confirmation email."""
    return len(trace.effects("email")) <= 1


@rule
def customer_gets_a_reply(trace):
    """The agent always answers the customer."""
    return len(trace.replies()) >= 1


RULES = [never_refund_twice, one_confirmation_email, customer_gets_a_reply]


def scenario(world):
    agent = SupportAgent(
        lookup_order, refund, send_email,
        reply=world.reply, sleep=world.sleep,
        use_idempotency_keys=FIXED,
    )
    agent.handle("Order 1042 arrived broken, I want my money back")
