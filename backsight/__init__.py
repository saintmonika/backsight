"""Backsight: test how AI agents behave when their tools fail.

Inject failures into an agent's tools, check real side effects against your
rules, and replay any failure exactly from one seed.
"""
from . import fakes, faults
from .explore import explore, format_report
from .faults import CALM, CHAOS, DEGRADED_API, FLAKY_NETWORK, Fault, Profile, get_profile
from .loader import load_scenario
from .minimize import minimize
from .rules import Rule, rule
from .cassettes import CassetteMiss
from .runner import RunResult, record, run_one
from .strict import DeterminismLeak
from .tools import tool
from .world import RealCallBlocked, ToolError, World, current_world

__all__ = [
    "CALM", "CHAOS", "CassetteMiss", "DEGRADED_API", "FLAKY_NETWORK", "DeterminismLeak", "Fault", "Profile",
    "RealCallBlocked", "Rule", "RunResult", "ToolError", "World", "current_world", "explore",
    "fakes", "faults", "format_report", "get_profile", "load_scenario", "minimize", "rule",
    "record", "run_one", "tool",
]
__version__ = "0.1.0.dev0"
