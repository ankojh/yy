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


def test_concurrent_matches_do_not_share_a_log_file(tmp_path):
    a, b = MatchLog(tmp_path), MatchLog(tmp_path)
    try:
        assert a.jsonl_path != b.jsonl_path
    finally:
        a.close()
        b.close()
