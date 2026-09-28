"""OpenAI commands each island; Jev remains the independent typed Arbiter."""

import asyncio
import json
from types import SimpleNamespace

from app import agents
from app.config import settings
from app.state import Action, initial_state


def test_commander_statement_guard_enforces_two_short_sentences():
    statement = " ".join(f"word{index}" for index in range(24))
    assert agents._trim_dialogue(statement) == statement
    assert agents._trim_dialogue(statement + " word24 word25") == statement + "…"
    assert agents._trim_dialogue("You crossed the line. We strike tonight. Expect fire.") == (
        "You crossed the line. We strike tonight."
    )


def test_commander_statement_guard_rejects_schema_and_indirect_language():
    assert not agents._natural_dialogue(
        'strike with arguments {"target": "infrastructure", "weapon": "drone_swarm"}.'
    )
    assert not agents._natural_dialogue("Expect international outrage and increased unrest.")
    assert not agents._natural_dialogue(
        "Your aggression brought this response. Stop now, or the next blow will be worse."
    )
    assert agents._natural_dialogue(
        "Your cabinet gambled that we would flinch. Tell them the wager has failed."
    )


def test_attack_dialogue_does_not_narrate_the_visible_weapon_or_target():
    action = Action(
        side="west",
        tool="strike",
        args={"weapon": "drone_swarm", "target": "infrastructure"},
    )
    assert not agents._dialogue_fits_action(
        action, "We are attacking your infrastructure with drones."
    )
    assert agents._dialogue_fits_action(
        action, "You called our restraint weakness. That miscalculation now belongs to you."
    )
    assert agents._direct_dialogue(action, 1) != agents._direct_dialogue(action, 2)


def response(response_id: str, payload: dict, model: str = "gpt-5-nano"):
    return SimpleNamespace(
        id=response_id,
        model=model,
        output=[],
        output_text=json.dumps(payload),
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


def hosted(monkeypatch, fake):
    monkeypatch.setattr(settings, "_force_mock", False)
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "typesafe_api_key", "tsf-test")
    monkeypatch.setattr(settings, "west_model", "gpt-5-nano")
    monkeypatch.setattr(settings, "east_model", "gpt-4.1-nano")
    monkeypatch.setattr(settings, "jev_model", "jev-latest")
    monkeypatch.setattr(agents, "_client", lambda: fake)


def candidate_for(state, side, phrase):
    legal = agents.legal_tools_for(state, side)
    candidates, _ = agents._legal_action_candidates(state, side, legal)
    return next(item for item in candidates if phrase in item["description"])


def commander_payload(candidate, message, **extra):
    return {
        "action_id": candidate["id"],
        "message": message,
        "demand": extra.get("demand", []),
        "concede": extra.get("concede", []),
        "confidence": extra.get("confidence", 0.87),
    }


def test_openai_chooses_and_voices_one_validated_action(monkeypatch):
    state = initial_state()
    state.world.phase = "conflict"
    candidate = candidate_for(state, "west", 'fortify with arguments {"domain": "naval"}')
    fake = FakeClient([
        response(
            "turn-1",
            commander_payload(candidate, "Our naval shield rises before your next salvo."),
        )
    ])
    hosted(monkeypatch, fake)

    action = asyncio.run(agents.decide(state, "west"))

    assert action.tool == "fortify"
    assert action.args == {
        "domain": "naval",
        "message": "Our naval shield rises before your next salvo.",
    }
    assert action.model == action.dialogue_model == "gpt-5-nano"
    assert action.decision_confidence == 0.87
    assert action.source == "live"

    request = fake.responses.requests[0]
    assert "tools" not in request
    assert "tool_choice" not in request
    assert "previous_response_id" not in request
    assert request["store"] is False
    assert request["text"]["format"]["name"] == "commander_turn"
    schema = request["text"]["format"]["schema"]
    assert candidate["id"] in schema["properties"]["action_id"]["enum"]
    developer_prompt = request["input"][0]["content"]
    assert "Choose exactly one action_id" in developer_prompt
    assert "one or two short sentences, 8–24 words" in developer_prompt
    private_input = json.loads(request["input"][1]["content"])
    assert private_input["private_turn_brief"]["tools_you_may_use_this_turn"] == action.legal
    assert candidate in private_input["legal_actions"]


def test_openai_replaces_mechanical_dialogue_without_changing_the_action(monkeypatch):
    state = initial_state()
    state.world.phase = "conflict"
    candidate = candidate_for(
        state, "west", 'strike with arguments {"target": "infrastructure"'
    )
    fake = FakeClient([
        response(
            "turn-mechanical",
            commander_payload(
                candidate,
                'strike with arguments {"target":"infrastructure","weapon":"drone_swarm"}.',
            ),
        )
    ])
    hosted(monkeypatch, fake)

    action = asyncio.run(agents.decide(state, "west"))

    assert action.tool == "strike"
    assert action.args["target"] == "infrastructure"
    assert "{" not in action.args["message"]
    assert "_" not in action.args["message"]
    assert action.args["message"] in agents.DIRECT_LINES["strike"]
    assert action.dialogue_model == "guarded-fallback"


def test_openai_can_choose_negotiation_positions_in_the_same_response(monkeypatch):
    state = initial_state()
    state.world.phase = "conflict"
    state.world.talks.open = True
    candidate = candidate_for(state, "west", "table terms with arguments")
    fake = FakeClient([
        response(
            "turn-terms",
            commander_payload(
                candidate,
                "These are our terms. Refuse them and the ceasefire ends.",
                demand=["kestrel_line", "halcyon_apology"],
                concede=["bellow_reef"],
            ),
        )
    ])
    hosted(monkeypatch, fake)

    action = asyncio.run(agents.decide(state, "west"))

    assert len(fake.responses.requests) == 1
    assert action.tool == "table_terms"
    assert action.args["demand"] == ["kestrel_line", "halcyon_apology"]
    assert action.args["concede"] == ["bellow_reef"]


def test_openai_failure_falls_back_to_a_complete_local_action(monkeypatch):
    fake = FakeClient([RuntimeError("commander service unavailable")])
    hosted(monkeypatch, fake)
    state = initial_state()
    state.world.phase = "conflict"

    action = asyncio.run(agents.decide(state, "west"))

    assert action.source == "fallback"
    assert action.model == "gpt-5-nano"
    assert action.dialogue_model == "fallback"
    assert action.tool in action.legal
    assert action.args["message"]


def test_dev_trace_records_one_openai_commander_request(monkeypatch):
    state = initial_state()
    state.world.phase = "conflict"
    candidate = candidate_for(state, "west", "hold with arguments {}")
    fake = FakeClient([
        response("wire-1", commander_payload(candidate, "We reload now. You burn next."))
    ])
    hosted(monkeypatch, fake)
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

    sent = [t for t in traces if t.get("stage") == "decision" and t["direction"] == "sent"]
    received = next(
        t for t in traces if t.get("stage") == "decision" and t["direction"] == "received"
    )
    usage = next(
        t for t in traces if t.get("stage") == "decision" and t["direction"] == "usage"
    )
    assert len(sent) == 1
    assert sent[0]["provider"] == "openai"
    assert sent[0]["endpoint"] == "/v1/responses"
    assert sent[0]["content"]["reasoning"] == {"effort": "minimal"}
    assert received["content"]["id"] == "wire-1"
    assert usage["uncached_input_tokens"] == 40
    assert usage["cache_hit_percent"] == 66.67
    assert usage["reasoning_tokens"] == 6
    assert usage["context_window_tokens"] == 400_000


def test_unknown_model_usage_omits_unverifiable_context_metrics():
    reply = response("wire-unknown", commander_payload({"id": "option_0"}, "Hold."))
    reply.model = "custom-model"
    payload = agents._usage_payload(reply, "custom-model")
    assert payload["input_tokens"] == 120
    assert "context_window_tokens" not in payload
    assert "context_utilization_percent" not in payload
    assert "remaining_context_tokens" not in payload


def test_gpt_4_1_nano_usage_uses_its_larger_context_window():
    reply = response(
        "wire-east",
        commander_payload({"id": "option_0"}, "Hold."),
        model="gpt-4.1-nano",
    )
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
    assert result["tension_delta"] == 0
    assert result["condemned"] is None
    assert result["condemnation"] == 0
    assert all(ruling["coherent"] for ruling in result["rulings"])
    assert "Aurelia held and regrouped" in result["bulletin"]
