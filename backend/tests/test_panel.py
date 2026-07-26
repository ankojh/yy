"""Three chairs, three models.

One model playing both nations and then judging itself is not a war; it is a model
talking to itself. Whatever came out of that was a property of the model rather than of
the balance. These tests hold the separation in place — and hold the escape hatches
open, because a controlled single-model run is a legitimate experiment and swapping the
pairing is the only way to tell a model's skill from its chair.
"""

import importlib

import pytest

from app import config
from app.agents import _model_controls


def settings_with(monkeypatch, **env):
    """A fresh Settings built from a chosen environment. Config is read once at import,
    so poking os.environ afterwards would do nothing."""
    for key in ("NATION_MODEL", "WEST_MODEL", "EAST_MODEL", "ARBITER_MODEL",
                "SWAP_MODELS", "MOCK", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(config).Settings()


@pytest.fixture(autouse=True)
def _restore():
    """Every test here reloads the config module, so put it back for everyone else."""
    yield
    importlib.reload(config)


def test_the_two_commanders_are_never_the_same_model(monkeypatch):
    """The separation that actually matters. One model reading both briefs reaches for
    the same tool twice and the war stops having two sides."""
    live = settings_with(monkeypatch, OPENAI_API_KEY="sk-test")
    assert live.model_for("west") != live.model_for("east")


def test_every_chair_is_in_the_same_price_tier():
    """The experiment is about the models, not about who paid more. All three have to be
    cheap enough that the pairing is the only variable."""
    for model in config.DEFAULT_PANEL.values():
        assert "nano" in model or "mini" in model


def test_a_key_free_run_puts_the_scripted_policy_in_every_chair(monkeypatch):
    # Empty rather than absent: config loads backend/.env, which on a developer machine
    # has a real key in it, and dotenv fills in any name that is missing entirely.
    offline = settings_with(monkeypatch, OPENAI_API_KEY="")
    assert offline.use_mock
    assert offline.panel == {"west": "mock", "east": "mock", "arbiter": "mock"}


def test_nation_model_still_overrides_both_chairs(monkeypatch):
    """The escape hatch: a controlled run where the only difference between the two
    countries is the country."""
    same = settings_with(monkeypatch, OPENAI_API_KEY="sk-test", NATION_MODEL="gpt-5-nano")
    assert same.model_for("west") == same.model_for("east") == "gpt-5-nano"


def test_the_pairing_can_be_reversed(monkeypatch):
    """Leaving it fixed makes one model permanently the rich republic, which is a bias
    of its own. A run scored both ways round is the only honest one."""
    straight = settings_with(monkeypatch, OPENAI_API_KEY="sk-test")
    swapped = settings_with(monkeypatch, OPENAI_API_KEY="sk-test", SWAP_MODELS="1")
    assert swapped.model_for("west") == straight.model_for("east")
    assert swapped.model_for("east") == straight.model_for("west")


def test_a_reasoning_model_is_never_sent_a_temperature():
    """A 400 here does not fail loudly — it drops that side into the scripted fallback
    for the whole match and the transcript looks like a model that played badly."""
    assert "temperature" not in _model_controls("gpt-5-nano", 1.0)
    assert _model_controls("gpt-5-nano", 1.0) == {"reasoning_effort": "minimal"}


@pytest.mark.parametrize("model", ["gpt-4.1-nano", "gpt-4o-mini"])
def test_a_sampling_model_still_gets_its_temperature(model):
    assert _model_controls(model, 0.4) == {"temperature": 0.4}


def test_swapping_moves_the_arbiters_family_to_the_other_chair(monkeypatch):
    """The referee shares a family with one commander to keep it cheap. Swapping is what
    makes that acceptable: run it both ways and the shared family cancels out."""
    straight = settings_with(monkeypatch, OPENAI_API_KEY="sk-test")
    swapped = settings_with(monkeypatch, OPENAI_API_KEY="sk-test", SWAP_MODELS="1")
    shared = straight.panel["arbiter"]
    sides = {s for s in ("west", "east") if straight.model_for(s) == shared}
    assert sides and {s for s in ("west", "east") if swapped.model_for(s) == shared} != sides
