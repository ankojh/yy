"""Intelligence visibility and treasury-funded resource allocation."""

import asyncio

from app.agents import _enemy_intelligence, _interception_intelligence
from app.engine import apply_action
from app.game import Game
from app.state import Action, initial_state
from app.tools import (
    INTEL_ESTIMATE_THRESHOLD,
    INTEL_EXACT_THRESHOLD,
    INVESTMENTS,
    available_tools,
    schemas_for,
)


def allocate(side: str, resource: str) -> Action:
    return Action(
        side=side,
        tool="allocate_resources",
        args={"resource": resource, "message": "Fund it."},
    )


def test_intelligence_progressively_reveals_enemy_state():
    state = initial_state()
    state.west.intelligence = INTEL_ESTIMATE_THRESHOLD - 1
    state.east.budget = 83

    coarse = _enemy_intelligence(state, "west")
    assert coarse["quality"] == "coarse"
    assert "estimated_operational_state" not in coarse
    assert "operational_state" not in coarse

    state.west.intelligence = INTEL_ESTIMATE_THRESHOLD
    estimated = _enemy_intelligence(state, "west")
    assert estimated["quality"] == "estimated"
    assert estimated["estimated_operational_state"]["treasury_usd_billions_nearest_10"] == 80
    assert "operational_state" not in estimated

    state.west.intelligence = INTEL_EXACT_THRESHOLD
    exact = _enemy_intelligence(state, "west")
    assert exact["quality"] == "exact"
    assert exact["operational_state"]["treasury_usd_billions"] == 83
    assert exact["operational_state"]["arsenal_rounds_left"] == state.east.arsenal


def test_interception_forecast_uses_the_same_intelligence_tiers():
    state = initial_state()
    state.west.intelligence = INTEL_ESTIMATE_THRESHOLD - 1
    coarse = _interception_intelligence(state, "west", "drone_swarm")
    assert "assessment" in coarse
    assert "fraction" not in coarse and "stopped" not in coarse

    state.west.intelligence = INTEL_ESTIMATE_THRESHOLD
    estimated = _interception_intelligence(state, "west", "drone_swarm")
    assert "estimated_interception_percent_nearest_10" in estimated
    assert "fraction" not in estimated and "stopped" not in estimated

    state.west.intelligence = INTEL_EXACT_THRESHOLD
    exact = _interception_intelligence(state, "west", "drone_swarm")
    assert {"cover", "fraction", "stopped", "salvo"} <= set(exact)


def test_treasury_can_buy_intelligence_and_reconstruction():
    state = initial_state()
    state.west.intelligence = 35
    budget = state.west.budget

    apply_action(state, allocate("west", "intelligence"), None)

    assert state.west.intelligence == 50
    assert state.west.budget == budget - INVESTMENTS["intelligence"]["price"]

    state.west.integrity = 70
    budget = state.west.budget
    apply_action(state, allocate("west", "infrastructure"), None)
    assert state.west.integrity == 82
    assert state.west.budget == budget - INVESTMENTS["infrastructure"]["price"]


def test_treasury_can_buy_defence_and_non_nuclear_resupply():
    state = initial_state()
    air = state.west.defenses["air"]
    drones = state.west.arsenal["drone_swarm"]

    apply_action(state, allocate("west", "air_defense"), None)
    apply_action(state, allocate("west", "drone_resupply"), None)

    assert state.west.defenses["air"] == air + 15
    assert state.west.arsenal["drone_swarm"] == drones + 2
    assert all("nuke" not in name for name in INVESTMENTS)


def test_unaffordable_investments_are_removed_from_tools_and_schema():
    state = initial_state()
    me = state.west
    poor = available_tools(me.arsenal, me.military, {}, budget=5)
    assert "allocate_resources" not in poor

    funded = available_tools(me.arsenal, me.military, {}, budget=8)
    assert "allocate_resources" in funded
    schema = schemas_for(
        ["allocate_resources"], me.arsenal, me.military, budget=8
    )[0]["function"]["parameters"]
    choices = schema["properties"]["resource"]["enum"]
    assert "intelligence" in choices
    assert "cruise_resupply" not in choices


def test_opening_intelligence_is_configurable():
    game = Game(write_log=False)
    asyncio.run(game.configure({"west": {"intelligence": 77}}))
    assert game.state.west.intelligence == 77
