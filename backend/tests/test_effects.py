"""The persistent effects that make the five non-strike tools worth a turn."""

from app.engine import BLOCKADE_TURNS, SANCTION_TURNS, apply_action, apply_upkeep
from app.state import Action, initial_state
from app.tools import available_tools, strike_price


def act(side, tool, **args):
    return Action(side=side, tool=tool, args={**args, "message": "."})


# ---------------------------------------------------------------- fortify


def test_fortify_shield_absorbs_most_of_the_next_strike_in_that_domain():
    def integrity_lost(shielded: bool) -> int:
        state = initial_state()
        if shielded:
            apply_action(state, act("east", "fortify", domain="air"), None)
        before = state.east.integrity
        apply_action(
            state, act("west", "strike", weapon="cruise_missile", target="infrastructure"), None
        )
        return before - state.east.integrity

    assert integrity_lost(True) < integrity_lost(False) * 0.5


def test_shield_is_spent_by_the_strike_it_absorbs():
    state = initial_state()
    apply_action(state, act("east", "fortify", domain="air"), None)
    assert state.east.effects.shield["air"] > 0
    apply_action(state, act("west", "strike", weapon="drone_swarm", target="infrastructure"), None)
    assert state.east.effects.shield["air"] == 0


def test_shield_protects_only_the_domain_it_covers():
    state = initial_state()
    apply_action(state, act("east", "fortify", domain="cyber"), None)
    before = state.east.integrity
    apply_action(state, act("west", "strike", weapon="cruise_missile", target="infrastructure"), None)
    air_damage = before - state.east.integrity
    assert state.east.effects.shield["cyber"] > 0  # untouched: wrong domain
    assert air_damage > 10


def test_a_warhead_ignores_hardened_cover():
    state = initial_state()
    apply_action(state, act("east", "fortify", domain="air"), None)
    before = state.east.integrity
    apply_action(state, act("west", "strike", weapon="nuke", target="infrastructure"), None)
    assert before - state.east.integrity > 50


def test_shield_lapses_after_its_duration():
    state = initial_state()
    apply_action(state, act("east", "fortify", domain="air"), None)
    for _ in range(3):
        apply_upkeep(state)
    assert "air" not in state.east.effects.shield


# ---------------------------------------------------------------- blockade


def test_blockade_bleeds_the_enemy_every_upkeep_for_several_turns():
    state = initial_state()
    apply_action(state, act("west", "blockade"), None)
    assert state.east.effects.blockaded == BLOCKADE_TURNS

    losses = []
    for _ in range(BLOCKADE_TURNS):
        before = state.east.integrity
        apply_upkeep(state)
        losses.append(before - state.east.integrity)
    assert all(loss > 0 for loss in losses)
    assert state.east.effects.blockaded == 0

    # And it stops once it lapses.
    before = state.east.integrity
    apply_upkeep(state)
    assert state.east.integrity >= before


def test_a_blockade_cannot_be_stacked_on_an_already_blockaded_enemy():
    state = initial_state()
    me = state.west
    assert "blockade" in available_tools(me.arsenal, me.military, me.cooldowns)
    assert "blockade" not in available_tools(
        me.arsenal, me.military, me.cooldowns, foe_blockaded=True
    )


# ---------------------------------------------------------------- intl_appeal


def test_credible_appeal_sanctions_the_enemy():
    state = initial_state()
    apply_action(state, act("west", "intl_appeal"), None)
    assert state.east.effects.sanctioned == SANCTION_TURNS


def test_sanctions_raise_the_dollar_price_of_striking():
    assert strike_price("cruise_missile", sanctioned=True) > strike_price("cruise_missile")


# ---------------------------------------------------------------- address_public


def test_addressing_the_public_directly_lowers_unrest():
    state = initial_state()
    state.east.unrest = 60
    apply_action(state, act("east", "address_public"), None)
    assert state.east.unrest < 60


def test_civilian_damage_reaches_public_unrest_directly():
    state = initial_state()
    before = state.east.unrest
    apply_action(state, act("west", "strike", weapon="drone_swarm", target="civilian"), None)
    assert state.east.unrest > before


# ---------------------------------------------------------------- hold


def test_hold_restores_readiness_and_clears_fatigue():
    state = initial_state()
    state.west.military = 30
    state.west.strike_streak = 2
    state.west.cooldowns = {"blockade": 3}

    apply_action(state, act("west", "hold"), None)
    assert state.west.military > 30
    assert state.west.strike_streak == 0
    assert state.west.cooldowns["blockade"] == 2  # refit burns cooldowns down faster


def test_hold_does_not_change_legacy_morale():
    state = initial_state()
    before = state.west.morale
    state.west.effects.morale_buffer = 0
    apply_action(state, act("west", "hold"), None)
    assert state.west.morale == before


# ---------------------------------------------------------------- cooldowns


def test_cooldowns_block_reuse_and_expire_on_upkeep():
    state = initial_state()
    apply_action(state, act("west", "blockade"), None)
    me = state.west
    assert "blockade" not in available_tools(me.arsenal, me.military, me.cooldowns)

    for _ in range(10):
        apply_upkeep(state)
    assert "blockade" not in me.cooldowns
