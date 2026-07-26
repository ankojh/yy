"""The war economy, the two pressures, and the asymmetry between the belligerents.

These are the mechanics that let a nation lose a war it was winning on the battlefield:
by running out of money, by being frozen out of trade, or by its own capital deciding
it had had enough.
"""

import pytest

from app.engine import (
    apply_action,
    apply_upkeep,
    check_end,
    trade_factor,
    turn_income,
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

    assert west.gdp > east.gdp                    # Aurelia is the rich one
    assert east.budget > west.budget              # ...but Korsav opens with more cash
    assert east.military > west.military
    assert west.standing > east.standing
    assert east.propaganda > west.propaganda
    assert west.defenses["cyber"] > east.defenses["cyber"]
    assert east.defenses["naval"] > west.defenses["naval"]
    assert east.arsenal["drone_swarm"] > west.arsenal["drone_swarm"]
    assert west.arsenal["cruise_missile"] > east.arsenal["cruise_missile"]


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


def test_holding_banks_credits_instead_of_spending_them():
    state = initial_state()
    state.west.budget = 20
    apply_action(state, act("west", "hold"), None)
    assert state.west.budget > 20


def test_a_sustained_deficit_is_paid_for_in_unrest():
    state = initial_state()
    state.west.budget = 0
    state.west.gdp = 0            # no income at all
    unrest = state.west.unrest

    for _ in range(3):
        apply_upkeep(state)

    assert state.west.effects.deficit_turns == 3
    assert state.west.budget == 0
    # And each consecutive deficit costs more than the one before it.
    assert state.west.unrest > unrest + 9


# ---------------------------------------------------------------- trade


def test_isolation_is_an_economic_weapon():
    state = initial_state()
    open_trade = turn_income(state.west)
    state.west.intl_pressure = 90
    assert turn_income(state.west) < open_trade
    assert trade_factor(state.west) < 0.5


def test_a_blockade_cuts_trade_on_top_of_everything_else():
    state = initial_state()
    before = turn_income(state.east)
    state.east.effects.blockaded = 3
    assert turn_income(state.east) < before


def test_an_export_economy_suffers_more_from_the_same_isolation():
    state = initial_state()
    for nation in (state.west, state.east):
        nation.intl_pressure = 80
    # Aurelia lives on trade; Korsav barely trades at all.
    assert trade_factor(state.west) < trade_factor(state.east)


def test_bombed_infrastructure_takes_the_economy_down_with_it():
    state = initial_state()
    gdp = state.east.gdp
    apply_action(
        state, act("west", "strike", weapon="cruise_missile", target="infrastructure"), None
    )
    assert state.east.gdp < gdp


def test_output_never_recovers_past_what_the_country_was_worth():
    state = initial_state()
    base = state.west.gdp_base
    state.west.gdp = base - 10
    for _ in range(20):
        apply_upkeep(state)
    assert state.west.gdp <= base


def test_output_slides_down_to_meet_wrecked_infrastructure():
    """Output above its own ceiling reads as a bug, and is one — it let a bombed-out
    country keep collecting income on works that were no longer standing."""
    state = initial_state()
    nation = state.west
    nation.integrity = 40           # half the country is rubble
    assert nation.gdp > nation.integrity

    # It slides rather than snapping — a factory hit today is still producing this week
    # — so give it enough turns to arrive, and check it is falling the whole way.
    seen = [nation.gdp]
    for _ in range(15):
        apply_upkeep(state)
        seen.append(nation.gdp)

    assert nation.gdp <= min(nation.gdp_base, nation.integrity)
    assert all(b <= a for a, b in zip(seen, seen[1:])), seen


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


def test_sustained_pressure_bleeds_standing():
    state = initial_state()
    state.west.intl_pressure = 90
    standing = state.west.standing
    apply_upkeep(state)
    assert state.west.standing < standing


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
    nation.propaganda, nation.effects.spin = 100, 99   # maximum muffling

    for _ in range(12):
        apply_upkeep(state)

    assert nation.unrest > 0, "a heavily spun public must still tire, only slower"


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


# ---------------------------------------------------------------- propaganda


def test_propaganda_buys_quiet_at_home_and_costs_credibility_abroad():
    state = initial_state()
    me = state.east
    me.unrest = 60
    pressure = me.intl_pressure

    apply_action(state, act("east", "propaganda"), None)
    assert me.unrest < 60
    assert me.intl_pressure > pressure
    assert me.effects.spin > 0


def test_a_running_campaign_muffles_everything_that_would_anger_the_public():
    def unrest_gained(spinning: bool) -> int:
        state = initial_state()
        if spinning:
            state.east.effects.spin = 3
        before = state.east.unrest
        apply_action(
            state, act("west", "strike", weapon="cruise_missile", target="civilian"), None
        )
        return state.east.unrest - before

    assert unrest_gained(True) < unrest_gained(False)


def test_a_spun_country_pays_more_for_the_same_act_abroad():
    def pressure_gained(spinning: bool) -> int:
        state = initial_state()
        if spinning:
            state.east.effects.spin = 3
        before = state.east.intl_pressure
        apply_action(
            state, act("east", "strike", weapon="cruise_missile", target="infrastructure"), None
        )
        return state.east.intl_pressure - before

    assert pressure_gained(True) > pressure_gained(False)


def test_a_campaign_the_record_contradicts_backfires_on_its_own_public():
    from app.state import Ruling

    state = initial_state()
    state.east.unrest = 50
    apply_action(
        state, act("east", "propaganda"), Ruling(side="east", effective=False, modifier=-1)
    )
    assert state.east.unrest > 50


def test_propaganda_hides_unrest_from_enemy_intelligence():
    state = initial_state()
    quiet, loud = state.east, state.west
    quiet.unrest = loud.unrest = 70
    quiet.propaganda, loud.propaganda = 90, 0
    quiet.effects.spin = 3

    # Same real unrest; the analysts read two different countries.
    assert quiet.coarse()["public_mood"] != loud.coarse()["public_mood"]
    assert quiet.spin_strength() > loud.spin_strength()


def test_a_campaign_costs_upkeep_every_turn_it_runs():
    from app.engine import turn_upkeep

    state = initial_state()
    cheap = turn_upkeep(state.west)      # Aurelia barely propagandises
    dear = turn_upkeep(state.east)       # Korsav runs a broadcaster
    assert dear > cheap


@pytest.mark.parametrize("tool", sorted(TOOL_META))
def test_every_tool_has_a_price_and_a_capacity_cost(tool):
    """No tool may quietly be free — that is how a dominant strategy gets in."""
    meta = TOOL_META[tool]
    assert {"cost", "price", "cooldown"} <= set(meta)
    assert meta["price"] >= 0 and meta["cost"] >= 0
