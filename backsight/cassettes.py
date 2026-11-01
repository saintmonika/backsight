"""Cassettes: real tool responses, recorded once and replayed forever.

Run `backsight record` once against the real API. Each response is saved,
keyed by the call's arguments. After that, runs replay the saved responses,
so nobody has to hand-write a fake Stripe and no run touches production.
"""
from __future__ import annotations

import json
import os
from typing import Any


class CassetteMiss(Exception):
    """The run asked for a response that was never recorded."""

    def __init__(self, tool: str, args: dict[str, Any], path: str):
        super().__init__(
            f"no recorded response for {tool}({_key(args)}) in {os.path.relpath(path)}. "
            f"Run `backsight record` again to capture it."
        )
        self.tool = tool
        self.call_args = args  # not .args: Exception uses that for its message


def _key(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, default=repr, separators=(",", ":"))


class Cassette:
    def __init__(self, path: str, tool: str):
        self.path = path
        self.tool = tool
        self.entries: dict[str, Any] = {}
        self.dirty = False
        if os.path.exists(path):
            with open(path) as fh:
                data = json.load(fh)
            for entry in data.get("entries", []):
                self.entries[_key(entry["args"])] = entry["result"]

    def get(self, args: dict[str, Any]) -> Any:
        key = _key(args)
        if key not in self.entries:
            raise CassetteMiss(self.tool, args, self.path)
        return json.loads(json.dumps(self.entries[key]))  # a fresh copy each time

    def put(self, args: dict[str, Any], result: Any) -> None:
        self.entries[_key(args)] = json.loads(json.dumps(result, default=repr))
        self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        entries = [{"args": json.loads(k), "result": v} for k, v in sorted(self.entries.items())]
        with open(self.path, "w") as fh:
            json.dump({"tool": self.tool, "entries": entries}, fh, indent=2)
            fh.write("\n")
        self.dirty = False
