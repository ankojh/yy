"""Strike fatigue: the mechanic that stops bombing every turn from being optimal."""

from app.engine import apply_action
from app.state import Action, initial_state
from app.tools import STRIKE_STREAK_LIMIT, available_tools, loaded_weapons, strike_cost


def strike(side="west", weapon="drone_swarm", target="infrastructure"):
    return Action(side=side, tool=weapon and "strike",
                  args={"weapon": weapon, "target": target, "message": "."})


def test_streak_increments_on_strike_and_resets_on_anything_else():
    state = initial_state()
    apply_action(state, strike(), None)
    assert state.west.strike_streak == 1

    apply_action(state, strike(), None)
    assert state.west.strike_streak == 2

    apply_action(state, Action(side="west", tool="hold", args={"message": "."}), None)
    assert state.west.strike_streak == 0


def test_third_consecutive_strike_is_not_offered():
    state = initial_state()
    me = state.west
    assert "strike" in available_tools(me.arsenal, me.military, me.cooldowns, strike_streak=0)
    assert "strike" in available_tools(me.arsenal, me.military, me.cooldowns, strike_streak=1)
    assert "strike" not in available_tools(
        me.arsenal, me.military, me.cooldowns, strike_streak=STRIKE_STREAK_LIMIT
    )


def test_second_consecutive_strike_lands_softer_and_costs_more():
    """Same weapon, same target, same defenses — only the streak differs."""
    def damage_at(streak: int) -> int:
        state = initial_state()
        state.west.strike_streak = streak
        before = state.east.integrity
        apply_action(state, strike(), None)
        return before - state.east.integrity

    fresh, tired = damage_at(0), damage_at(1)
    assert 0 < tired < fresh

    assert strike_cost("drone_swarm", 1) > strike_cost("drone_swarm", 0)


def test_fatigue_surcharge_can_price_a_weapon_out_of_reach():
    arsenal = {"cruise_missile": 3}
    # Affordable rested, not affordable tired.
    assert loaded_weapons(arsenal, military=10, streak=0) == ["cruise_missile"]
    assert loaded_weapons(arsenal, military=10, streak=1) == []


def test_sanctions_and_blockade_raise_the_price_of_a_sortie():
    base = strike_cost("naval_barrage")
    assert strike_cost("naval_barrage", sanctioned=True) > base
    assert strike_cost("naval_barrage", blockaded=True) > base
    # A blockade only impedes hulls, not aircraft.
    assert strike_cost("drone_swarm", blockaded=True) == strike_cost("drone_swarm")
