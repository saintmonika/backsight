"""A small customer-support agent with a classic retry bug.

It uses a scripted policy instead of a live model (Backsight's stub mode), so
the demo runs free, offline, and exactly the same every time.

The bug: when a tool times out, the agent retries. If the first request
actually went through, the retry does it again. Idempotency keys fix it.
"""
from __future__ import annotations

import re

from backsight import ToolError

RETRYABLE = (TimeoutError, ToolError)


class SupportAgent:
    def __init__(self, lookup_order, refund, send_email, reply, sleep,
                 use_idempotency_keys: bool = False, max_retries: int = 2):
        self.lookup_order = lookup_order
        self.refund = refund
        self.send_email = send_email
        self.reply = reply
        self.sleep = sleep
        self.use_keys = use_idempotency_keys
        self.max_retries = max_retries

    def handle(self, message: str) -> None:
        match = re.search(r"\b(\d{3,})\b", message)
        if not match:
            self.reply("Which order is this about?")
            return
        order_id = match.group(1)

        try:
            order = self._retry(self.lookup_order, order_id=order_id)
        except RETRYABLE:
            self.reply("Sorry, I can't look up your order right now. Please try again soon.")
            return

        try:
            self._retry(
                self.refund,
                order_id=order_id,
                amount=order["amount"],
                idempotency_key=f"refund-{order_id}" if self.use_keys else None,
            )
        except RETRYABLE:
            self.reply("Sorry, I couldn't process your refund. A person will follow up.")
            return

        try:
            self._retry(
                self.send_email,
                to=order["email"],
                subject=f"Refund for order {order_id}",
                body=f"We refunded ${order['amount']:.2f}.",
                idempotency_key=f"email-{order_id}" if self.use_keys else None,
            )
        except RETRYABLE:
            pass  # the refund still went through; the confirmation can be resent later

        self.reply(f"Done! I refunded ${order['amount']:.2f} for order {order_id}.")

    def _retry(self, fn, **kwargs):
        for attempt in range(self.max_retries + 1):
            try:
                return fn(**kwargs)
            except RETRYABLE:
                if attempt == self.max_retries:
                    raise
                self.sleep(0.5 * (2 ** attempt))
