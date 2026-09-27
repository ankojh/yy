"""OpenAI commanders and the Jev Arbiter remain independently configurable."""

import pytest

from app import config
from app.agents import _response_controls


def settings_with(monkeypatch, **env):
    """A fresh Settings built from a chosen environment."""
    for key in ("APP_ENV", "NATION_MODEL", "WEST_MODEL", "EAST_MODEL", "JEV_MODEL",
                "SWAP_MODELS", "MOCK", "OPENAI_API_KEY", "TYPESAFE_API_KEY",
                "REVEAL", "TURN_PAUSE", "BEAT"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return config.Settings()


def test_default_live_pacing_is_nine_seconds_per_action(monkeypatch):
    live = settings_with(monkeypatch)
    assert live.reveal == 9.0
    assert live.turn_pause == 2.0
    assert live.beat == 0.3


def test_the_two_islands_default_to_different_commanders(monkeypatch):
    live = settings_with(monkeypatch, OPENAI_API_KEY="sk-test")
    assert live.model_for("west") != live.model_for("east")


def test_the_referee_is_not_a_commander_model():
    assert config.DEFAULT_PANEL["arbiter"].startswith("jev-")
    assert config.DEFAULT_PANEL["arbiter"] not in {
        config.DEFAULT_PANEL["west"], config.DEFAULT_PANEL["east"]
    }


def test_development_uses_the_mixed_provider_panel(monkeypatch):
    live = settings_with(
        monkeypatch, APP_ENV="development",
        OPENAI_API_KEY="sk-test", TYPESAFE_API_KEY="tsf-test",
    )
    assert not live.use_mock
    assert not live.use_mock_decisions
    assert not live.use_mock_dialogue
    assert not live.use_mock_arbiter
    assert live.panel == config.DEFAULT_PANEL


def test_a_key_free_hosted_run_puts_the_scripted_policy_in_every_chair(monkeypatch):
    # Empty rather than absent: config loads backend/.env, which on a developer machine
    # has a real key in it, and dotenv fills in any name that is missing entirely.
    offline = settings_with(monkeypatch, OPENAI_API_KEY="", TYPESAFE_API_KEY="")
    assert offline.use_mock
    assert offline.use_mock_decisions
    assert offline.use_mock_dialogue
    assert offline.use_mock_arbiter
    assert offline.panel == {"west": "mock", "east": "mock", "arbiter": "mock"}


def test_provider_keys_fall_back_independently(monkeypatch):
    openai_only = settings_with(
        monkeypatch, OPENAI_API_KEY="sk-test", TYPESAFE_API_KEY=""
    )
    assert openai_only.panel["west"] != "mock"
    assert openai_only.panel["arbiter"] == "mock"
    assert not openai_only.use_mock_decisions
    assert not openai_only.use_mock_dialogue

    jev_only = settings_with(
        monkeypatch, OPENAI_API_KEY="", TYPESAFE_API_KEY="tsf-test"
    )
    assert jev_only.panel["west"] == "mock"
    assert jev_only.panel["east"] == "mock"
    assert jev_only.panel["arbiter"] == "jev-latest"
    assert jev_only.use_mock_decisions
    assert jev_only.use_mock_dialogue


def test_nation_model_still_overrides_both_chairs(monkeypatch):
    """A controlled run can use the same commander model for both countries."""
    same = settings_with(
        monkeypatch, OPENAI_API_KEY="sk-test", NATION_MODEL="gpt-5-nano",
    )
    assert same.model_for("west") == same.model_for("east") == "gpt-5-nano"


def test_the_pairing_can_be_reversed(monkeypatch):
    """Commander assignment can be reversed without changing the Jev Arbiter."""
    straight = settings_with(monkeypatch, OPENAI_API_KEY="sk-test")
    swapped = settings_with(
        monkeypatch, OPENAI_API_KEY="sk-test", SWAP_MODELS="1"
    )
    assert swapped.model_for("west") == straight.model_for("east")
    assert swapped.model_for("east") == straight.model_for("west")


def test_a_reasoning_model_is_never_sent_a_temperature():
    """A 400 here does not fail loudly — it drops that side into the scripted fallback
    for the whole match and the transcript looks like a model that played badly."""
    assert "temperature" not in _response_controls("gpt-5-nano", 1.0)
    assert _response_controls("gpt-5-nano", 1.0) == {
        "reasoning": {"effort": "minimal"}
    }


@pytest.mark.parametrize("model", ["gpt-4.1-nano", "gpt-4o-mini"])
def test_a_sampling_model_still_gets_its_temperature(model):
    assert _response_controls(model, 0.4) == {"temperature": 0.4}


def test_swapping_never_changes_the_independent_referee(monkeypatch):
    straight = settings_with(
        monkeypatch, OPENAI_API_KEY="sk-test", TYPESAFE_API_KEY="tsf-test"
    )
    swapped = settings_with(
        monkeypatch, OPENAI_API_KEY="sk-test", TYPESAFE_API_KEY="tsf-test",
        SWAP_MODELS="1",
    )
    assert straight.panel["arbiter"] == swapped.panel["arbiter"] == "jev-latest"
