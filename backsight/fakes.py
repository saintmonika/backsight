"""Ready-made in-memory fakes that record real effects.

A fake method is named like the tool it backs. It calls world.effect(...)
whenever something really happens, which is what rules check.
"""
from __future__ import annotations

from typing import Any


class Fake:
    def __init__(self, world: Any) -> None:
        self.world = world


class Payments(Fake):
    """Refunds, with optional idempotency keys like Stripe's."""

    def __init__(self, world: Any) -> None:
        super().__init__(world)
        self._count = 0
        self._by_key: dict[str, dict] = {}

    def refund(self, order_id: str, amount: float, idempotency_key: str | None = None) -> dict:
        if idempotency_key is not None and idempotency_key in self._by_key:
            return self._by_key[idempotency_key]  # same request again: no new refund
        self._count += 1
        refund_id = f"re_{self._count}"
        self.world.effect("refund", order_id=order_id, amount=amount, refund_id=refund_id)
        result = {"refund_id": refund_id, "status": "succeeded", "amount": amount}
        if idempotency_key is not None:
            self._by_key[idempotency_key] = result
        return result


class Email(Fake):
    """Outgoing email, with optional idempotency keys."""

    def __init__(self, world: Any) -> None:
        super().__init__(world)
        self._sent_keys: set[str] = set()

    def send_email(self, to: str, subject: str, body: str, idempotency_key: str | None = None) -> dict:
        if idempotency_key is not None and idempotency_key in self._sent_keys:
            return {"status": "sent"}
        if idempotency_key is not None:
            self._sent_keys.add(idempotency_key)
        self.world.effect("email", to=to, subject=subject)
        return {"status": "sent"}


class Records(Fake):
    """A read-only lookup table. Subclass with data via Records.with_data({...})."""

    data: dict[str, dict] = {}

    @classmethod
    def with_data(cls, data: dict[str, dict], name: str = "Records") -> type:
        return type(name, (cls,), {"data": data})

    def get(self, key: str) -> dict:
        if key not in self.data:
            raise KeyError(f"no record {key!r}")
        return dict(self.data[key])

    def __getattr__(self, tool_name: str):
        # Lets one Records fake back any lookup tool, e.g. lookup_order(order_id=...)
        if tool_name.startswith("_"):
            raise AttributeError(tool_name)

        def lookup(**kwargs: Any) -> dict:
            (key,) = kwargs.values()
            return self.get(key)

        return lookup
