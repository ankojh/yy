"""The player's council can shape the war without ever taking a combat turn."""

import pytest

from app.game import SUPPORT_ACTIONS, Game


async def started_game() -> Game:
    game = Game(write_log=False)
    await game.reset()
    await game.ignite(["trawler"])
    return game


def request(game: Game, kind: str, side: str = "west"):
    pending = game._make_support_request(side, kind)
    game.state.world.council_request = pending
    return pending


@pytest.mark.asyncio
async def test_stabilization_fund_moves_real_money_and_spends_council_funds():
    game = await started_game()
    opening_nation = game.state.west.budget
    opening_council = game.state.world.council_budget
    pending = request(game, "stabilization")

    await game.support(pending.id, True)

    cost = SUPPORT_ACTIONS["stabilization"]["cost"]
    assert game.state.west.budget == opening_nation + cost
    assert game.state.world.council_budget == opening_council - cost
    assert game.state.world.council_history[-1]["targets"] == ["west"]
    assert game.state.world.council_request is None
    assert game.log[-2].type == "support_response"


@pytest.mark.asyncio
async def test_a_request_can_only_back_the_island_that_made_it():
    game = await started_game()
    west = dict(game.state.west.defenses)
    east = dict(game.state.east.defenses)
    pending = request(game, "defensive", "west")

    await game.support(pending.id, True)

    assert game.state.world.council_budget == 72 - SUPPORT_ACTIONS["defensive"]["cost"]
    assert game.state.west.defenses["air"] == west["air"] + 12
    assert game.state.east.defenses == east


@pytest.mark.asyncio
async def test_weapons_are_supplied_without_creating_a_player_combat_action():
    game = await started_game()
    drones = game.state.east.arsenal["drone_swarm"]
    cyber = game.state.east.arsenal["cyber_strike"]
    history = len(game.state.world.history)
    pending = request(game, "arms", "east")

    await game.support(pending.id, True)

    assert game.state.east.arsenal["drone_swarm"] == drones + 2
    assert game.state.east.arsenal["cyber_strike"] == cyber + 1
    assert len(game.state.world.history) == history
    assert game.state.world.council_history[-1]["kind"] == "arms"


@pytest.mark.asyncio
async def test_council_cannot_spend_money_it_does_not_have():
    game = await started_game()
    game.state.world.council_budget = 5
    before = game.state.west.model_dump()
    pending = request(game, "humanitarian")

    await game.support(pending.id, True)

    assert game.state.west.model_dump() == before
    assert not game.state.world.council_history
    assert game.log[-2].type == "note"


@pytest.mark.asyncio
async def test_an_island_can_ask_for_diplomatic_cover():
    game = await started_game()
    game.state.west.intl_pressure = 30
    pending = request(game, "cover")

    await game.support(pending.id, True)
    assert game.state.west.intl_pressure == 18


@pytest.mark.asyncio
async def test_the_council_can_decline_without_spending_anything():
    game = await started_game()
    before = game.state.world.council_budget
    pending = request(game, "arms", "east")

    await game.support(pending.id, False)

    assert game.state.world.council_budget == before
    assert game.state.world.council_request is None
    assert game.state.world.council_history[-1]["approved"] is False


@pytest.mark.asyncio
async def test_unsolicited_support_is_ignored():
    game = await started_game()
    before = game.state.model_dump()

    await game.support("not-a-real-request", True)

    assert game.state.model_dump() == before


@pytest.mark.asyncio
async def test_support_is_unavailable_before_the_crisis_starts():
    game = Game(write_log=False)
    await game.reset()
    game.state.world.council_request = game._make_support_request("west", "arms")
    await game.support(game.state.world.council_request.id, True)
    assert game.state.world.council_budget == 72
    assert not game.state.world.council_history


@pytest.mark.asyncio
async def test_a_live_game_places_a_need_based_request_before_the_council():
    async def emit(_):
        return None

    game = Game(emit=emit, write_log=False)
    await game.reset()
    await game.ignite(["trawler"])
    game.state.world.turn = 2

    await game._maybe_request_support()

    pending = game.state.world.council_request
    assert pending is not None
    assert pending.side in ("west", "east")
    assert pending.kind in SUPPORT_ACTIONS
    assert game.log[-1].type == "support_request"
