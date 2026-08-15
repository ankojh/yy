"""Finite war funds, the two pressures, and asymmetric public unrest."""

import pytest

from app.engine import (
    apply_action,
    apply_upkeep,
    check_end,
)
from app.state import Action, initial_state
from app.tools import (
    TOOL_META,
    WEAPONS,
    available_tools,
    loaded_weapons,
    strike_pressure,
    strike_price,
)


def act(side, tool, **args):
    return Action(side=side, tool=tool, args={**args, "message": "."})


# ---------------------------------------------------------------- asymmetry


def test_the_two_nations_do_not_start_from_the_same_position():
    """A symmetric war is the same war twice. Every axis here has to actually differ."""
    state = initial_state()
    west, east = state.west, state.east

    assert east.budget > west.budget
    assert east.military > west.military
    assert west.traits.unrest_sensitivity > east.traits.unrest_sensitivity
    assert west.defenses["cyber"] > east.defenses["cyber"]
    assert east.defenses["naval"] > west.defenses["naval"]
    assert east.arsenal["drone_swarm"] > west.arsenal["drone_swarm"]
    assert west.arsenal["cruise_missile"] > east.arsenal["cruise_missile"]
    assert east.arsenal["narrative"] > west.arsenal["narrative"]


def test_the_same_act_costs_the_two_nations_different_amounts():
    """Traits have to be load-bearing, not decoration on a profile."""
    def pressure_after(side):
        state = initial_state()
        before = state.nation(side).intl_pressure
        apply_action(
            state, act(side, "strike", weapon="cruise_missile", target="civilian"), None
        )
        return state.nation(side).intl_pressure - before

    def unrest_after(side):
        state = initial_state()
        foe = "east" if side == "west" else "west"
        before = state.nation(side).unrest
        apply_action(
            state, act(foe, "strike", weapon="cruise_missile", target="civilian"), None
        )
        return state.nation(side).unrest - before

    # Korsav is assumed to be the villain; Aurelia is given the benefit of the doubt.
    assert pressure_after("east") > pressure_after("west")
    # Aurelia's free press reacts to the same atrocity far more loudly.
    assert unrest_after("west") > unrest_after("east")


# ---------------------------------------------------------------- budget


def test_every_offensive_move_is_paid_for_in_credits():
    state = initial_state()
    before = state.west.budget
    apply_action(state, act("west", "strike", weapon="cruise_missile", target="military"), None)
    assert state.west.budget == before - strike_price("cruise_missile")


def test_a_treasury_that_cannot_pay_withdraws_the_tools_that_cost_money():
    state = initial_state()
    me = state.west
    assert "blockade" in available_tools(me.arsenal, me.military, me.cooldowns, budget=100)

    broke = available_tools(me.arsenal, me.military, me.cooldowns, budget=1)
    assert "blockade" not in broke
    assert "fortify" not in broke
    assert "strike" not in broke        # even the cheapest round costs more than 1
    assert "hold" in broke              # standing down is always free


def test_the_cheapest_round_is_the_last_one_a_broke_nation_can_fire():
    arsenal = {w: 3 for w in WEAPONS}
    rich = loaded_weapons(arsenal, military=100, budget=999)
    poor = loaded_weapons(arsenal, military=100, budget=strike_price("cyber_strike"))
    assert set(rich) == set(WEAPONS)
    assert poor == ["cyber_strike"]


def test_holding_does_not_mint_new_war_funds():
    state = initial_state()
    state.west.budget = 20
    apply_action(state, act("west", "hold"), None)
    assert state.west.budget == 20


def test_an_empty_treasury_is_paid_for_in_unrest():
    state = initial_state()
    state.west.budget = 0
    unrest = state.west.unrest

    for _ in range(3):
        apply_upkeep(state)

    assert state.west.effects.deficit_turns == 1
    assert state.west.budget == 0
    assert state.west.unrest > unrest


# ---------------------------------------------------------------- finite funds


def test_international_pressure_drains_treasury_directly():
    state = initial_state()
    state.west.intl_pressure = 90
    before = state.west.budget
    apply_upkeep(state)
    assert state.west.budget == before - 3


def test_a_blockade_drains_treasury_without_a_gdp_meter():
    state = initial_state()
    state.east.effects.blockaded = 3
    before = state.east.budget
    apply_upkeep(state)
    assert state.east.budget == before - 4


def test_infrastructure_strike_does_not_touch_legacy_gdp():
    state = initial_state()
    before_infra, legacy_gdp = state.east.integrity, state.east.gdp
    apply_action(state, act("west", "strike", weapon="cruise_missile", target="infrastructure"), None)
    assert state.east.integrity < before_infra
    assert state.east.gdp == legacy_gdp


# ---------------------------------------------------------------- international pressure


def test_heavier_ordnance_on_softer_targets_costs_more_pressure():
    """The whole pressure model in one assertion: what you fired and what you hit."""
    assert strike_pressure("cruise_missile", "military") < strike_pressure(
        "cruise_missile", "infrastructure"
    )
    assert strike_pressure("cruise_missile", "infrastructure") < strike_pressure(
        "cruise_missile", "civilian"
    )
    assert strike_pressure("drone_swarm", "civilian") < strike_pressure(
        "cruise_missile", "civilian"
    )
    assert strike_pressure("nuke", "military") > strike_pressure("cruise_missile", "civilian")


def test_pressure_lands_on_the_attacker_not_on_the_world():
    state = initial_state()
    apply_action(state, act("west", "strike", weapon="cruise_missile", target="civilian"), None)
    assert state.west.intl_pressure > 0
    assert state.east.intl_pressure == 0


def test_an_appeal_moves_pressure_across_the_strait():
    state = initial_state()
    state.west.intl_pressure = 40
    apply_action(state, act("west", "intl_appeal"), None)
    assert state.west.intl_pressure < 40      # the only instrument that buys relief
    assert state.east.intl_pressure > 0


def test_pressure_fades_when_you_stop_earning_it():
    state = initial_state()
    state.west.intl_pressure = 30
    apply_upkeep(state)
    assert state.west.intl_pressure < 30


def test_sustained_pressure_spends_finite_funds():
    state = initial_state()
    state.west.intl_pressure = 90
    budget = state.west.budget
    apply_upkeep(state)
    assert state.west.budget < budget


# ---------------------------------------------------------------- public unrest


def test_the_public_tires_of_the_war_on_its_own():
    """Both publics, even the one being told the war is going well."""
    state = initial_state()
    west, east = state.west.unrest, state.east.unrest
    for _ in range(4):
        apply_upkeep(state)
    assert state.west.unrest > west
    assert state.east.unrest > east
    # Aurelia's free press gets there considerably faster.
    assert state.west.unrest - west > state.east.unrest - east


def test_slow_drift_is_never_rounded_out_of_existence():
    """A sixth of a point per turn has to be slow, not zero — the carry does that."""
    state = initial_state()
    nation = state.east
    nation.unrest = 0
    for _ in range(12):
        apply_upkeep(state)

    assert nation.unrest > 0


def test_civilian_strikes_inflame_both_publics():
    state = initial_state()
    theirs, mine = state.east.unrest, state.west.unrest
    apply_action(state, act("west", "strike", weapon="cruise_missile", target="civilian"), None)
    assert state.east.unrest > theirs      # grief
    assert state.west.unrest > mine        # shame


def test_addressing_the_public_settles_the_streets():
    state = initial_state()
    state.west.unrest = 50
    apply_action(state, act("west", "address_public"), None)
    assert state.west.unrest < 50


def test_a_government_that_loses_its_streets_loses_the_war():
    state = initial_state()
    state.world.phase = "conflict"
    state.east.unrest = 100
    outcome = check_end(state)
    assert outcome and "crowd" in outcome
    assert state.world.loser == "east"


# ---------------------------------------------------------------- narrative operations


def test_narrative_round_raises_enemy_unrest_and_costs_pressure():
    state = initial_state()
    me = state.east
    foe = state.west
    rounds, unrest = me.arsenal["narrative"], foe.unrest
    pressure = me.intl_pressure

    apply_action(state, act("east", "propaganda"), None)
    assert me.arsenal["narrative"] == rounds - 1
    assert foe.unrest > unrest
    assert me.intl_pressure > pressure


def test_a_campaign_the_record_contradicts_backfires_on_its_own_public():
    from app.state import Ruling

    state = initial_state()
    state.east.unrest = 50
    apply_action(
        state, act("east", "propaganda"), Ruling(side="east", effective=False, modifier=-1)
    )
    assert state.east.unrest > 50


def test_narrative_operations_respect_each_islands_public_nature():
    state = initial_state()
    west_before = state.west.unrest
    apply_action(state, act("east", "propaganda"), None)
    west_gain = state.west.unrest - west_before

    state = initial_state()
    east_before = state.east.unrest
    apply_action(state, act("west", "propaganda"), None)
    east_gain = state.east.unrest - east_before
    assert west_gain > east_gain


def test_narrative_tool_disappears_when_its_magazine_is_empty():
    state = initial_state()
    state.west.arsenal["narrative"] = 0
    tools = available_tools(state.west.arsenal, state.west.military, {}, budget=100)
    assert "propaganda" not in tools


@pytest.mark.parametrize("tool", sorted(TOOL_META))
def test_every_tool_has_a_price_and_a_capacity_cost(tool):
    """No tool may quietly be free — that is how a dominant strategy gets in."""
    meta = TOOL_META[tool]
    assert {"cost", "price", "cooldown"} <= set(meta)
    assert meta["price"] >= 0 and meta["cost"] >= 0
