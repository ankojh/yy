"""Match logs have to be readable against the rules that produced them, and must never
contain a key or a model's private reasoning."""

import asyncio
import json

from app.config import BALANCE_VERSION
from app.game import Game
from app.matchlog import MatchLog


def test_every_decision_records_its_provenance():
    async def go():
        game = Game(seed=2, write_log=False)
        await game.reset()
        await game.ignite(["trawler"])
        await game.run()
        return game

    game = asyncio.run(go())
    decisions = [e for e in game.log if e.type == "decision"]
    messages = [e for e in game.log if e.type == "message"]
    assert len(decisions) == len(messages)

    for ev in decisions:
        assert ev.payload["source"] in ("live", "mock", "fallback")
        assert isinstance(ev.payload["legal"], list) and ev.payload["legal"]
        assert isinstance(ev.payload["strike_streak"], int)
        assert "effects" in ev.payload


def test_the_declared_action_was_always_legal():
    async def go():
        game = Game(seed=5, write_log=False)
        await game.reset()
        await game.ignite(["airspace"])
        await game.run()
        return game

    game = asyncio.run(go())
    pending = {}
    for ev in game.log:
        if ev.type == "decision":
            pending[ev.payload["side"]] = ev.payload["legal"]
        elif ev.type == "message":
            assert ev.payload["tool"] in pending[ev.payload["side"]]


def test_rulings_record_which_flags_fired():
    async def go():
        game = Game(seed=1, write_log=False)
        await game.reset()
        await game.ignite(["envoy"])
        await game.run()
        return game

    game = asyncio.run(go())
    rulings = [e for e in game.log if e.type == "ruling"]
    assert rulings
    for ev in rulings:
        flags = ev.payload["flags"]
        assert {"coherent", "exploits_weakness", "adapts",
                "overstated", "repetitive", "futile"} <= set(flags)


def test_match_log_stamps_the_balance_version_and_models(tmp_path):
    log = MatchLog(tmp_path, meta={"balance_version": BALANCE_VERSION,
                                   "nation_model": "mock", "seed": 4})
    log.close()

    first = json.loads(log.jsonl_path.read_text().splitlines()[0])
    assert first["type"] == "meta"
    assert first["payload"]["balance_version"] == BALANCE_VERSION
    assert first["payload"]["nation_model"] == "mock"
    assert BALANCE_VERSION in log.md_path.read_text()


def test_raw_llm_io_goes_to_a_separate_jsonl_file(tmp_path):
    log = MatchLog(tmp_path, meta={"west_model": "test-west"})
    payload = {
        "agent": "west", "direction": "sent", "model": "test-west", "turn": 1,
        "content": {"messages": [{"role": "user", "content": "the full prompt"}]},
    }
    log.write_llm_trace(payload)
    log.close()

    rows = [json.loads(line) for line in log.llm_path.read_text().splitlines()]
    assert rows[0] == {"type": "meta", "payload": {"west_model": "test-west"}}
    assert rows[1]["type"] == "llm_trace"
    assert rows[1]["agent"] == "west"
    assert rows[1]["content"] == payload["content"]
    assert "the full prompt" not in log.jsonl_path.read_text()


def test_usage_log_has_counters_but_never_prompt_content(tmp_path):
    log = MatchLog(tmp_path, meta={"west_model": "test-west"})
    log.write_llm_usage({
        "agent": "west", "direction": "usage", "model": "test-west", "turn": 1,
        "api": "responses", "input_tokens": 120, "cached_input_tokens": 80,
        "output_tokens": 14, "total_tokens": 134,
        "content": {"input": "private canonical state"},
    })
    log.close()

    rows = [json.loads(line) for line in log.usage_path.read_text().splitlines()]
    assert rows[1]["type"] == "llm_usage"
    assert rows[1]["cached_input_tokens"] == 80
    assert "content" not in rows[1]
    assert "private canonical state" not in log.usage_path.read_text()


def test_logs_never_contain_the_api_key(tmp_path, monkeypatch):
    secret = "sk-test-do-not-log-me"
    monkeypatch.setattr("app.config.settings.openai_api_key", secret, raising=False)

    async def go():
        game = Game(seed=6, write_log=False)
        game._file = MatchLog(tmp_path, meta={"balance_version": BALANCE_VERSION})
        await game.reset()
        return game

    game = asyncio.run(go())
    for ev in game.log:
        assert secret not in ev.model_dump_json()


def test_dev_view_streams_sanitized_traces_outside_the_replay_log(monkeypatch):
    streamed = []
    secret = "sk-test-must-never-cross-the-socket"
    monkeypatch.setattr("app.config.settings.app_env", "development")
    monkeypatch.setattr("app.config.settings.llm_debug", True)
    monkeypatch.setattr("app.config.settings.openai_api_key", secret)

    async def emit(event):
        streamed.append(event)

    async def go():
        game = Game(emit=emit, write_log=False)
        await game.set_dev_view(True)
        await game.record_llm_trace({
            "agent": "west",
            "direction": "sent",
            "model": "gpt-5-nano",
            "api": "responses",
            "content": {
                "messages": [{"role": "user", "content": "private state"}],
                "api_key": secret,
                "nested": {"message": f"request accidentally contained {secret}"},
                "headers": {"set-cookie": "transient-cookie-value"},
            },
        })
        await game.record_llm_trace({
            "agent": "west",
            "direction": "received",
            "model": "gpt-5-nano",
            "api": "responses",
            "content": {"tool_call": {"name": "hold", "arguments": {}}},
        })
        return game

    game = asyncio.run(go())
    traces = [event for event in streamed if event.type == "llm_trace"]
    assert [event.payload["direction"] for event in traces] == ["sent", "received"]
    assert traces[0].payload["content"]["api_key"] == "[redacted]"
    assert traces[0].payload["content"]["headers"]["set-cookie"] == "[redacted]"
    assert secret not in traces[0].model_dump_json()
    assert traces[1].payload["elapsed_ms"] is not None
    assert not [event for event in game.log if event.type == "llm_trace"]


def test_dev_view_cannot_be_enabled_in_production(monkeypatch):
    streamed = []
    monkeypatch.setattr("app.config.settings.app_env", "production")
    monkeypatch.setattr("app.config.settings.llm_debug", True)

    async def emit(event):
        streamed.append(event)

    async def go():
        game = Game(emit=emit, write_log=False)
        await game.set_dev_view(True)
        await game.record_llm_trace({"agent": "west", "direction": "sent"})

    asyncio.run(go())
    status = next(event for event in streamed if event.type == "dev_status")
    assert status.payload["available"] is False
    assert status.payload["enabled"] is False
    assert not [event for event in streamed if event.type == "llm_trace"]


def test_concurrent_matches_do_not_share_a_log_file(tmp_path):
    a, b = MatchLog(tmp_path), MatchLog(tmp_path)
    try:
        assert a.jsonl_path != b.jsonl_path
    finally:
        a.close()
        b.close()
