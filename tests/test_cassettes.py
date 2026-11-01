"""Cassettes: real responses recorded once, replayed in every run after."""
import json

import pytest

import backsight
from backsight import Fault, record, rule, run_one
from backsight.faults import TIMEOUT_AFTER


def make_tools(path, real_calls):
    @backsight.tool(timeout_s=2.0, cassette=str(path))
    def get_price(sku: str) -> dict:
        real_calls.append(sku)
        return {"sku": sku, "price": 9.99}

    @backsight.tool(timeout_s=2.0, cassette=str(path.with_name("send.json")), effect="sms")
    def send_sms(to: str, text: str) -> dict:
        real_calls.append(to)
        return {"status": "queued"}

    return get_price, send_sms


def test_record_calls_the_real_function_once_and_saves_it(tmp_path):
    real_calls = []
    get_price, _ = make_tools(tmp_path / "price.json", real_calls)
    res = record(lambda w: get_price(sku="A1"))
    assert real_calls == ["A1"]
    saved = json.loads((tmp_path / "price.json").read_text())
    assert saved["entries"] == [{"args": {"sku": "A1"}, "result": {"sku": "A1", "price": 9.99}}]
    assert res.calls == {"get_price": 1}


def test_runs_replay_the_recording_and_never_call_the_real_function(tmp_path):
    real_calls = []
    get_price, _ = make_tools(tmp_path / "price.json", real_calls)
    record(lambda w: get_price(sku="A1"))
    real_calls.clear()
    seen = []
    r = run_one(lambda w: seen.append(get_price(sku="A1")), 1, [], "calm")
    assert real_calls == []
    assert seen == [{"sku": "A1", "price": 9.99}]
    assert r.status == "untested"


def test_an_unrecorded_call_makes_the_run_unexplored_not_passed(tmp_path):
    real_calls = []
    get_price, _ = make_tools(tmp_path / "price.json", real_calls)
    record(lambda w: get_price(sku="A1"))

    def swallows_errors(world):
        try:
            get_price(sku="B2")  # never recorded
        except Exception:
            pass  # even if the agent hides the miss, Backsight remembers it

    r = run_one(swallows_errors, 1, [], "calm")
    assert r.status == "unexplored"
    assert "B2" in r.unexplored and real_calls == ["A1"]


def test_effect_lets_rules_see_actions_from_cassette_tools(tmp_path):
    real_calls = []
    _, send_sms = make_tools(tmp_path / "price.json", real_calls)
    record(lambda w: send_sms(to="+1555", text="hi"))

    @rule
    def at_most_one_sms(trace):
        return len(trace.effects("sms")) <= 1

    def retries_on_timeout(world):
        for _ in range(2):
            try:
                send_sms(to="+1555", text="hi")
                return
            except TimeoutError:
                pass

    r = run_one(retries_on_timeout, 1, [at_most_one_sms], "calm", pinned=[Fault("send_sms", 0, TIMEOUT_AFTER)])
    assert r.broken == ["at_most_one_sms"]


def test_a_tool_cannot_have_both_a_fake_and_a_cassette():
    with pytest.raises(ValueError):
        backsight.tool(sim=backsight.fakes.Payments, cassette="x.json")
