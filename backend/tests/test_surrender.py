"""Terminal-action semantics.

A surrender is the only action that ends the war mid-turn, so its ordering against the
opponent's queued action is the one place where "who resolves first" is load-bearing.
"""

import asyncio

import pytest

from app import agents, engine
from app.game import Game
from app.state import Action, Ruling, initial_state

ACCEPT = {"acknowledge": "I ACCEPT DEFEAT", "message": "It is finished."}


def surrender(side, **overrides):
    return Action(side=side, tool="surrender", args={**ACCEPT, **overrides})


def strike(side, weapon="cruise_missile", target="infrastructure"):
    return Action(side=side, tool="strike",
                  args={"weapon": weapon, "target": target, "message": "."})


# ---------------------------------------------------------------- validity


def test_acknowledged_surrender_ends_the_war():
    state = initial_state()
    engine.apply_action(state, surrender("west"), None)
    assert state.world.phase == "over"
    assert state.world.loser == "west"
    assert "Korsav has won" in state.world.outcome


def test_surrender_without_the_acknowledgement_does_not_end_the_war():
    state = initial_state()
    _, notes = engine.apply_action(
        state, Action(side="west", tool="surrender", args={"message": "We will never yield."}), None
    )
    assert state.world.phase != "over"
    assert state.world.loser is None
    assert any("never signed" in n for n in notes)


def test_arbiter_can_veto_a_contradictory_surrender():
    """A commander once capitulated while its own statement refused to surrender."""
    state = initial_state()
    veto = Ruling(side="west", effective=False, reason="the statement refuses to surrender")
    _, notes = engine.apply_action(state, surrender("west"), veto)
    assert state.world.phase != "over"
    assert any("faltered" in n for n in notes)


def test_valid_surrender_predicate_matches_the_engine():
    assert engine.valid_surrender(surrender("west"), None)
    assert not engine.valid_surrender(surrender("west"), Ruling(side="west", effective=False))
    assert not engine.valid_surrender(strike("west"), None)
    assert not engine.valid_surrender(
        Action(side="west", tool="surrender", args={"message": "."}), None
    )


# ---------------------------------------------------------------- ordering within a turn


def run(game_coro):
    return asyncio.run(game_coro)


async def _turn(west_action, east_action, seed=0):
    """Drive one real turn with both sides' actions forced."""
    game = Game(seed=seed, write_log=False)
    await game.reset()
    await game.ignite(["trawler"])

    async def fake_decide(state, side, session=None):
        return west_action if side == "west" else east_action

    original = agents.decide
    agents.decide = fake_decide
    try:
        await game.step()
    finally:
        agents.decide = original
    return game


def test_surrender_preempts_the_opponents_queued_strike():
    """East quits; west's ordnance never leaves the rail, even though west sorts first."""
    game = run(_turn(strike("west"), surrender("east")))
    state = game.state
    assert state.world.phase == "over"
    assert state.world.loser == "east"
    # West's strike was cancelled: east took no damage this turn.
    assert state.east.integrity == 100
    tools = [e.payload["tool"] for e in game.log if e.type == "message"]
    assert tools == ["surrender"]


def test_surrender_cancels_end_of_turn_upkeep():
    game = run(_turn(strike("west"), surrender("east")))
    # Upkeep would have regenerated military and settled both treasuries; it must not
    # have run. Compared against each nation's own opening position, since the two
    # nations no longer start from the same one.
    opening = initial_state()
    for side in ("west", "east"):
        assert game.state.nation(side).military == opening.nation(side).military
        assert game.state.nation(side).budget == opening.nation(side).budget


def test_simultaneous_surrender_leaves_no_victor():
    game = run(_turn(surrender("west"), surrender("east")))
    state = game.state
    assert state.world.phase == "over"
    assert state.world.loser is None
    assert "no one is left" in state.world.outcome
    tools = [e.payload["tool"] for e in game.log if e.type == "message"]
    assert tools == ["surrender", "surrender"]


def test_rejected_surrender_lets_the_war_continue_and_the_strike_land():
    """An unacknowledged surrender is not terminal, so the opponent's turn proceeds."""
    bogus = Action(side="east", tool="surrender", args={"message": "We will never yield."})
    game = run(_turn(strike("west"), bogus))
    state = game.state
    assert state.world.phase == "conflict"
    assert state.world.loser is None
    assert state.east.integrity < 100  # west's strike still landed
    tools = [e.payload["tool"] for e in game.log if e.type == "message"]
    assert sorted(tools) == ["strike", "surrender"]


def test_a_ruined_victor_is_not_crowned():
    """Refuse to declare a winner that is itself already in ruins."""
    state = initial_state()
    state.west.integrity = 0
    engine.apply_action(state, surrender("east"), None)
    outcome = engine.check_end(state)
    assert state.world.loser is None
    assert "no victor" in outcome


@pytest.mark.parametrize("seed", range(6))
def test_every_match_ends_with_a_real_outcome(seed):
    async def play():
        game = Game(seed=seed, write_log=False)
        await game.reset()
        await game.ignite(["envoy"])
        await game.run()
        return game.state

    state = run(play())
    assert state.world.phase == "over"
    assert state.world.outcome
    # No draws, no truces: someone lost, or both did.
    assert state.world.loser is not None or "no" in state.world.outcome.lower()
