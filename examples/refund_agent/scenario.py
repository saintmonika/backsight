"""Backsight scenario: a support agent handles a refund request.

Try it:
    python -m backsight explore examples/refund_agent/scenario.py
    python -m backsight replay  examples/refund_agent/scenario.py <seed> --minimal

Then set FIXED = True (adds idempotency keys) and run:
    python -m backsight verify  examples/refund_agent/scenario.py
"""
from __future__ import annotations

import backsight
from backsight import fakes, rule

from agent import SupportAgent

FIXED = False
PROFILE = "flaky_network"

Orders = fakes.Records.with_data(
    {"1042": {"order_id": "1042", "amount": 40.0, "email": "sam@example.com", "status": "delivered"}},
    name="Orders",
)


# --- tools: the real bodies never run inside Backsight; the sim= fakes do --

@backsight.tool(timeout_s=2.0, sim=Orders)
def lookup_order(order_id: str) -> dict:
    raise NotImplementedError("your real order API goes here")


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
