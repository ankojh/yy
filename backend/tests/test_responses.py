"""Jev owns decisions; OpenAI can only voice the action Jev locked."""

import asyncio
import json
from types import SimpleNamespace

from app import agents
from app.config import settings
from app.state import Action, initial_state


def test_commander_statement_guard_allows_longer_declarations():
    statement = " ".join(f"word{index}" for index in range(48))
    assert agents._trim(statement) == statement
    assert agents._trim(statement + " word48 word49") == statement + "…"


def response(response_id: str, message: str = "We hold."):
    return SimpleNamespace(
        id=response_id,
        model="gpt-5-nano",
        output=[],
        output_text=json.dumps({"message": message}),
        usage=SimpleNamespace(
            input_tokens=120,
            input_tokens_details=SimpleNamespace(
                cached_tokens=80, cache_write_tokens=16,
            ),
            output_tokens=14,
            output_tokens_details=SimpleNamespace(reasoning_tokens=6),
            total_tokens=134,
        ),
    )


class FakeResponses:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    async def create(self, **request):
        self.requests.append(request)
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeClient:
    def __init__(self, replies):
        self.responses = FakeResponses(replies)


def hosted(monkeypatch, fake, fake_jev):
    monkeypatch.setattr(settings, "_force_mock", False)
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "typesafe_api_key", "tsf-test")
    monkeypatch.setattr(settings, "west_model", "gpt-5-nano")
    monkeypatch.setattr(settings, "east_model", "gpt-4.1-nano")
    monkeypatch.setattr(settings, "jev_model", "jev-latest")
    monkeypatch.setattr(agents, "_client", lambda: fake)
    monkeypatch.setattr(agents.jev, "decide", fake_jev)


def chooser(requests, match):
    async def fake_jev(**request):
        requests.append(request)
        criteria = request["questions"]["action"]["criteria"]
        choice = next(key for key, value in criteria.items() if match in value)
        return {
            "model": "jev-1.13.0",
            "answers": {
                "action": {"type": "choice", "choice": choice, "confidence": 0.87}
            },
            "usage": {"input_tokens": 220, "output_tokens": 1},
        }
    return fake_jev


def test_jev_locks_action_and_openai_only_writes_dialogue(monkeypatch):
    jev_requests = []
    fake = FakeClient([response("voice-1", "Our naval shield rises before your next salvo.")])
    hosted(
        monkeypatch,
        fake,
        chooser(jev_requests, 'fortify with arguments {"domain": "naval"}'),
    )
    state = initial_state()
    state.world.phase = "conflict"

    action = asyncio.run(agents.decide(state, "west"))

    assert action.tool == "fortify"
    assert action.args == {
        "domain": "naval",
        "message": "Our naval shield rises before your next salvo.",
    }
    assert action.model == "jev-latest"
    assert action.dialogue_model == "gpt-5-nano"
    assert action.decision_confidence == 0.87
    assert action.source == "live"
    assert jev_requests[0]["state"]["turn"]["tools_you_may_use_this_turn"] == action.legal

    request = fake.responses.requests[0]
    assert "tools" not in request
    assert "tool_choice" not in request
    assert "previous_response_id" not in request
    assert request["store"] is False
    assert request["text"]["format"]["name"] == "commander_dialogue"
    locked = json.loads(request["input"][1]["content"])["locked_action"]
    assert locked == {"action": "fortify", "arguments": {"domain": "naval"}}


def test_table_terms_are_a_second_typed_jev_decision(monkeypatch):
    requests = []

    async def fake_jev(**request):
        requests.append(request)
        if "action" in request["questions"]:
            criteria = request["questions"]["action"]["criteria"]
            choice = next(k for k, v in criteria.items() if v.startswith("table terms"))
            answers = {"action": {"type": "choice", "choice": choice, "confidence": 0.7}}
        else:
            answers = {
                article: {
                    "type": "choice",
                    "choice": "concede" if article == "bellow_reef" else "demand",
                    "confidence": 0.8,
                }
                for article in request["questions"]
            }
        return {"answers": answers, "usage": {"input_tokens": 100, "output_tokens": 2}}

    fake = FakeClient([response("voice-terms", "These are our terms. Answer them.")])
    hosted(monkeypatch, fake, fake_jev)
    state = initial_state()
    state.world.phase = "conflict"
    state.world.talks.open = True

    action = asyncio.run(agents.decide(state, "west"))

    assert len(requests) == 2
    assert action.tool == "table_terms"
    assert action.args["concede"] == ["bellow_reef"]
    assert "bellow_reef" not in action.args["demand"]
    assert set(action.args["demand"]) == set(requests[1]["questions"]) - {"bellow_reef"}


def test_jev_failure_uses_scripted_action_but_openai_voices_it(monkeypatch):
    async def broken_jev(**_request):
        raise RuntimeError("decision service unavailable")

    fake = FakeClient([response("voice-fallback", "The course is set; prepare yourselves.")])
    hosted(monkeypatch, fake, broken_jev)
    state = initial_state()
    state.world.phase = "conflict"

    action = asyncio.run(agents.decide(state, "west"))

    assert action.source == "fallback"
    assert action.model == "jev-latest"
    assert action.dialogue_model == "gpt-5-nano"
    assert action.args["message"] == "The course is set; prepare yourselves."
    locked = json.loads(fake.responses.requests[0]["input"][1]["content"])["locked_action"]
    assert locked["action"] == action.tool


def test_openai_failure_never_changes_jevs_locked_action(monkeypatch):
    requests = []
    fake = FakeClient([RuntimeError("voice service unavailable")])
    hosted(
        monkeypatch,
        fake,
        chooser(requests, 'fortify with arguments {"domain": "cyber"}'),
    )
    state = initial_state()
    state.world.phase = "conflict"

    action = asyncio.run(agents.decide(state, "west"))

    assert action.tool == "fortify"
    assert action.args["domain"] == "cyber"
    assert action.args["message"]
    assert action.source == "live"
    assert action.dialogue_model == "fallback"


def test_dev_trace_separates_jev_decision_from_openai_dialogue(monkeypatch):
    requests = []
    fake = FakeClient([response("wire-1", "We are holding this line.")])
    hosted(monkeypatch, fake, chooser(requests, "hold with arguments {}"))
    state = initial_state()
    state.world.phase = "conflict"
    traces = []

    async def capture(payload):
        traces.append(payload)

    async def go():
        token = agents.bind_trace(capture)
        try:
            await agents.decide(state, "west")
        finally:
            agents.unbind_trace(token)

    asyncio.run(go())

    decision_sent = next(
        t for t in traces if t.get("stage") == "decision" and t["direction"] == "sent"
    )
    dialogue_sent = next(
        t for t in traces if t.get("stage") == "dialogue" and t["direction"] == "sent"
    )
    dialogue_received = next(
        t for t in traces if t.get("stage") == "dialogue" and t["direction"] == "received"
    )
    usage = next(
        t for t in traces if t.get("stage") == "dialogue" and t["direction"] == "usage"
    )
    assert decision_sent["provider"] == "typesafe"
    assert decision_sent["endpoint"] == "/v1/systemone"
    assert dialogue_sent["provider"] == "openai"
    assert dialogue_sent["content"] == fake.responses.requests[0]
    assert dialogue_sent["content"]["reasoning"] == {"effort": "minimal"}
    assert dialogue_received["content"]["id"] == "wire-1"
    assert usage["uncached_input_tokens"] == 40
    assert usage["cache_write_tokens"] == 16
    assert usage["cache_hit_percent"] == 66.67
    assert usage["reasoning_tokens"] == 6
    assert usage["context_window_tokens"] == 400_000
    assert usage["context_utilization_percent"] == 0.0335
    assert usage["remaining_context_tokens"] == 399_866


def test_unknown_model_usage_omits_unverifiable_context_metrics():
    reply = response("wire-unknown")
    reply.model = "custom-model"
    payload = agents._usage_payload(reply, "custom-model")
    assert payload["input_tokens"] == 120
    assert "context_window_tokens" not in payload
    assert "context_utilization_percent" not in payload
    assert "remaining_context_tokens" not in payload


def test_gpt_4_1_nano_usage_uses_its_larger_context_window():
    reply = response("wire-east")
    reply.model = "gpt-4.1-nano"
    payload = agents._usage_payload(reply, "gpt-4.1-nano")
    assert payload["context_window_tokens"] == 1_047_576
    assert payload["remaining_context_tokens"] == 1_047_442
    assert payload["context_utilization_percent"] == 0.0128


def test_arbiter_uses_one_stateless_jev_decision_request(monkeypatch):
    answers = {}
    for side in ("west", "east"):
        answers.update({
            f"{side}_coherent": {"type": "noul", "noul": 0.91},
            f"{side}_exploits_weakness": {"type": "noul", "noul": 0.18},
            f"{side}_adapts": {"type": "noul", "noul": 0.22},
            f"{side}_overstated": {"type": "noul", "noul": 0.12},
        })
    answers.update({
        "condemned": {"type": "choice", "choice": "none", "confidence": 0.8},
        "condemnation": {"type": "choice", "choice": "level_1", "confidence": 0.8},
        "tension": {"type": "choice", "choice": "zero", "confidence": 0.9},
    })
    reply = {
        "model": "jev-1.13.0",
        "answers": answers,
        "usage": {"input_tokens": 240, "output_tokens": 11},
    }
    requests = []

    async def fake_decide(**request):
        requests.append(request)
        return reply

    monkeypatch.setattr(settings, "_force_mock", False)
    monkeypatch.setattr(settings, "typesafe_api_key", "tsf-test")
    monkeypatch.setattr(settings, "jev_model", "jev-latest")
    monkeypatch.setattr(agents.jev, "decide", fake_decide)
    state = initial_state()
    state.world.phase = "conflict"
    actions = [
        Action(side=side, tool="hold", args={"message": "Hold."})
        for side in ("west", "east")
    ]

    result = asyncio.run(agents.arbitrate(state, actions))
    assert len(requests) == 1
    request = requests[0]
    assert request["model"] == "jev-latest"
    assert request["api_key"] == "tsf-test"
    assert request["state"]["declared_actions"] == [
        {"side": side, "tool": "hold", "args": {"message": "Hold."}}
        for side in ("west", "east")
    ]
    assert len(request["questions"]) == 11
    assert request["questions"]["west_coherent"]["type"] == "noul"
    assert request["questions"]["condemned"]["type"] == "choice"
    assert request["questions"]["tension"]["type"] == "choice"
    assert result["tension_delta"] == 0
    assert result["condemned"] is None
    assert result["condemnation"] == 0
    assert all(ruling["coherent"] for ruling in result["rulings"])
    assert "Aurelia held and regrouped" in result["bulletin"]
