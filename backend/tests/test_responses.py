"""Hosted Responses orchestration stays isolated, bounded, and measurable."""

import asyncio
import json
from types import SimpleNamespace

from app import agents
from app.config import settings
from app.state import Action, initial_state


def response(response_id: str, arguments: dict, output_text: str = ""):
    return SimpleNamespace(
        id=response_id,
        output=[
            SimpleNamespace(
                type="function_call",
                name="decide_turn",
                call_id=f"call-{response_id}",
                arguments=json.dumps(arguments),
            )
        ],
        output_text=output_text,
        usage=SimpleNamespace(
            input_tokens=120,
            input_tokens_details=SimpleNamespace(cached_tokens=80),
            output_tokens=14,
            total_tokens=134,
        ),
    )


class FakeResponses:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    async def create(self, **request):
        self.requests.append(request)
        return next(self.replies)


class FakeClient:
    def __init__(self, replies):
        self.responses = FakeResponses(replies)


def hosted(monkeypatch, fake, chain_turns=2):
    monkeypatch.setattr(settings, "_force_mock", False)
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "west_model", "gpt-5-nano")
    monkeypatch.setattr(settings, "east_model", "gpt-4.1-nano")
    monkeypatch.setattr(settings, "arbiter_model", "gpt-5-nano")
    monkeypatch.setattr(settings, "response_chain_turns", chain_turns)
    monkeypatch.setattr(agents, "_client", lambda: fake)


def test_commander_chain_is_short_and_restarts_from_canonical_state(monkeypatch):
    replies = [
        response(f"resp-{number}", {"action": "hold", "message": "We hold.", "intent": "recover"})
        for number in range(1, 4)
    ]
    fake = FakeClient(replies)
    hosted(monkeypatch, fake, chain_turns=2)
    state = initial_state()
    state.world.phase = "conflict"
    session = agents.ResponseSession()

    async def go():
        for turn in range(1, 4):
            state.world.turn = turn
            action = await agents.decide(state, "west", session)
            assert action.tool == "hold"

    asyncio.run(go())
    first, second, third = fake.responses.requests
    assert "previous_response_id" not in first
    assert second["previous_response_id"] == "resp-1"
    assert "previous_response_id" not in third
    assert "how_this_war_ends" in first["input"]
    assert second["input"][0] == {
        "type": "function_call_output",
        "call_id": "call-resp-1",
        "output": "The declared action was accepted and resolved.",
    }
    assert "authoritative_current_state" in second["input"][1]["content"]
    assert "how_this_war_ends" in third["input"]
    assert first["tools"] == second["tools"] == third["tools"]
    assert first["prompt_cache_key"] == second["prompt_cache_key"] == third["prompt_cache_key"]


def test_commander_sessions_never_share_response_ids(monkeypatch):
    fake = FakeClient([
        response("west-1", {"action": "hold", "message": "West holds."}),
        response("east-1", {"action": "hold", "message": "East holds."}),
    ])
    hosted(monkeypatch, fake)
    state = initial_state()
    state.world.phase = "conflict"

    async def go():
        await agents.decide(state, "west", agents.ResponseSession())
        await agents.decide(state, "east", agents.ResponseSession())

    asyncio.run(go())
    assert all("previous_response_id" not in req for req in fake.responses.requests)
    assert fake.responses.requests[0]["prompt_cache_key"] != fake.responses.requests[1]["prompt_cache_key"]


def test_arbiter_responses_request_is_stateless(monkeypatch):
    ruling = {
        "rulings": [
            {
                "side": side,
                "coherent": True,
                "exploits_weakness": False,
                "adapts": False,
                "overstated": False,
                "reason": "ordinary restraint",
            }
            for side in ("west", "east")
        ],
        "tension_delta": 0,
        "condemned": None,
        "condemnation": 0,
        "bulletin": "Both capitals pause as the council watches.",
    }
    reply = response("arbiter-1", {}, output_text=json.dumps(ruling))
    fake = FakeClient([reply])
    hosted(monkeypatch, fake)
    state = initial_state()
    state.world.phase = "conflict"
    actions = [
        Action(side=side, tool="hold", args={"message": "Hold."})
        for side in ("west", "east")
    ]

    result = asyncio.run(agents.arbitrate(state, actions))
    request = fake.responses.requests[0]
    assert result == ruling
    assert request["store"] is False
    assert "previous_response_id" not in request
    assert request["text"]["format"]["strict"] is True
