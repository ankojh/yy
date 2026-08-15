"""The dead, and the batteries that decide how many of them there are.

Two mechanics that used to be invisible. Casualties are not a meter and cannot lose you
the war on their own — they are the reason the meters move, and the specific thing an
appeal or a broadcast gets to point at. Interception is what a defence number actually
buys, and it has to be computed in one place so that the rounds dying on screen are the
rounds that died in the arithmetic.
"""

import pytest

from app.engine import (
    FORTIFY_GAIN,
    SHIELD_ABSORPTION,
    apply_action,
    apply_upkeep,
    strike_interception,
)
from app.state import Action, initial_state
from app.tools import (
    BLOCKADE_DEATHS,
    INTERCEPT_CEILING,
    WEAPONS,
    casualties_from,
    intercepted_fraction,
    intercepted_rounds,
    place_struck,
)


def act(side, tool, **args):
    return Action(side=side, tool=tool, args={**args, "message": "."})


def strike(state, weapon="cruise_missile", target="infrastructure", side="west"):
    return apply_action(state, act(side, "strike", weapon=weapon, target=target), None)


# ---------------------------------------------------------------- the dead


def test_where_you_aim_decides_how_many_die():
    """A missile into a housing block and the same missile into a runway are not the
    same act, and the only place that difference is visible is the body count."""
    civilian = initial_state()
    military = initial_state()
    strike(civilian, target="civilian")
    strike(military, target="military")
    assert civilian.east.casualties > military.east.casualties * 5


def test_a_warhead_is_not_on_the_same_scale_as_anything_else():
    conventional = initial_state()
    nuclear = initial_state()
    strike(conventional, weapon="cruise_missile", target="civilian")
    strike(nuclear, weapon="nuke", target="civilian")
    assert nuclear.east.casualties > conventional.east.casualties * 50


def test_the_toll_has_no_ceiling():
    """Every other number in this simulation is clamped to a hundred. This one is a
    count of people and clamping it would be the wrong kind of tidy."""
    state = initial_state()
    for _ in range(4):
        state.west.arsenal["cruise_missile"] = 5
        state.west.budget = 300
        state.west.strike_streak = 0
        strike(state, target="civilian")
    assert state.east.casualties > 100


def test_the_toll_accumulates_rather_than_replacing():
    state = initial_state()
    strike(state, target="civilian")
    first = state.east.casualties
    state.west.strike_streak = 0
    strike(state, target="civilian")
    assert state.east.casualties > first


def test_the_dead_are_reported_as_a_delta_the_ui_can_show():
    state = initial_state()
    deltas, _ = strike(state, target="civilian")
    dead = [d for d in deltas if d["field"] == "casualties"]
    assert len(dead) == 1
    assert dead[0]["side"] == "east"
    assert dead[0]["delta"] > 0
    assert dead[0]["value"] == state.east.casualties


def test_a_blockade_kills_without_anybody_firing():
    """Nobody is shelling anybody and people die anyway: the medicine that did not come
    in on the ship. It is the quiet half of what a blockade is."""
    state = initial_state()
    state.east.effects.blockaded = 3
    apply_upkeep(state)
    assert state.east.casualties == BLOCKADE_DEATHS


def test_the_body_count_is_never_hidden_from_the_enemy():
    """Every other reading of the other side is a coarse band. Bodies are counted by
    everybody, which is what makes them usable in an appeal."""
    state = initial_state()
    strike(state, target="civilian")
    assert state.east.coarse()["civilian_dead"] == state.east.casualties


# ---------------------------------------------------------------- what was hit


def test_hitting_a_protected_place_is_recorded_with_a_name_and_a_toll():
    state = initial_state()
    state.world.turn = 1
    # Walk the deterministic place table until a protected one comes up, so the test
    # does not depend on which entry happens to be first.
    for turn in range(1, 12):
        state.world.turn = turn
        state.west.arsenal["cruise_missile"] = 5
        state.west.budget = 300
        state.west.strike_streak = 0
        strike(state, target="civilian")
        if any(a.protected for a in state.world.atrocities):
            break

    hit = next(a for a in state.world.atrocities if a.protected)
    assert hit.victim == "east"
    assert hit.attacker == "west"
    assert hit.dead > 0
    assert hit.place and hit.place != ""


def test_a_school_costs_the_attacker_more_abroad_than_a_substation():
    """The surcharge for a protected place is charged to the capital that fired, which
    is the whole reason an atrocity is a strategic fact and not only a sad one."""
    plain = initial_state()
    protected = initial_state()
    # The place is a deterministic function of the turn, so walk turns until one of each
    # kind turns up rather than hard-coding the engine's salt arithmetic in here.
    kinds = {}
    for turn in range(1, 20):
        probe = initial_state()
        probe.world.turn = turn
        strike(probe, target="civilian")
        kinds[turn] = probe.world.atrocities[-1].protected
    unprotected_turn = next(t for t, p in kinds.items() if not p)
    protected_turn = next(t for t, p in kinds.items() if p)

    plain.world.turn = unprotected_turn
    protected.world.turn = protected_turn
    strike(plain, target="civilian")
    strike(protected, target="civilian")
    assert protected.west.intl_pressure > plain.west.intl_pressure


def test_the_place_table_is_deterministic():
    """A match has to replay identically, so nothing here may reach for the RNG."""
    assert place_struck("civilian", 41) == place_struck("civilian", 41)


# ---------------------------------------------------------------- interception


@pytest.mark.parametrize("weapon", [w for w in WEAPONS if w != "nuke"])
def test_more_cover_stops_more_of_the_salvo(weapon):
    assert intercepted_fraction(60, weapon) > intercepted_fraction(20, weapon)


def test_nothing_is_ever_airtight():
    assert intercepted_fraction(100, "drone_swarm") <= INTERCEPT_CEILING


def test_a_warhead_is_not_intercepted():
    """Defences are irrelevant to a warhead, which is the entire point of holding one."""
    assert intercepted_fraction(100, "nuke") == 0
    assert intercepted_rounds(100, "nuke") == 0


def test_a_salvo_is_never_wiped_out_entirely():
    """A sortie that is silently annihilated reads as a bug. The fraction already does
    the work of blunting the damage; the count is there to be watched."""
    for weapon, spec in WEAPONS.items():
        assert intercepted_rounds(100, weapon) < spec["salvo"]


def test_the_renderer_and_the_engine_are_told_the_same_number():
    """The one invariant that matters here: what dies on screen is what died in the
    arithmetic. Both read this function; neither rolls its own dice."""
    state = initial_state()
    shot = strike_interception(state, "west", "drone_swarm")
    assert shot["cover"] == state.east.defenses["air"]
    assert shot["salvo"] == WEAPONS["drone_swarm"]["salvo"]
    assert shot["stopped"] == intercepted_rounds(shot["cover"], "drone_swarm")


def test_a_defended_domain_takes_less_damage():
    open_door = initial_state()
    walled = initial_state()
    walled.east.defenses["air"] = 70
    open_door.east.defenses["air"] = 0
    strike(open_door, weapon="cruise_missile")
    strike(walled, weapon="cruise_missile")
    assert walled.east.integrity > open_door.east.integrity


def test_fortifying_raises_the_share_that_is_shot_down_permanently():
    """The complaint about fortify was that you could not tell whether it had done
    anything. It now moves the standing defence, not only the one-shot cover."""
    state = initial_state()
    before = state.east.defenses["air"]
    apply_action(state, act("east", "fortify", domain="air"), None)
    assert state.east.defenses["air"] >= before + FORTIFY_GAIN
    assert intercepted_fraction(state.east.defenses["air"], "drone_swarm") > intercepted_fraction(
        before, "drone_swarm"
    )
    # ...and the one-shot cover is there on top of it.
    assert strike_interception(state, "west", "drone_swarm")["hardened"]


def test_hardened_cover_absorbs_most_of_one_strike_and_is_spent():
    state = initial_state()
    apply_action(state, act("east", "fortify", domain="air"), None)
    strike(state, weapon="cruise_missile")
    assert state.east.effects.shield.get("air", 0) == 0
    assert SHIELD_ABSORPTION < 0.3   # the point of the mechanic, not an incidental


def test_interception_is_reported_so_a_blunted_strike_has_a_visible_cause():
    state = initial_state()
    state.east.defenses["air"] = 60
    _, notes = strike(state, weapon="drone_swarm")
    assert any("defences destroy" in n for n in notes)


def test_a_stopped_round_kills_nobody():
    """Damage and casualties come off the same number, so a salvo that is mostly shot
    down must also be mostly bloodless."""
    thin = initial_state()
    thick = initial_state()
    thin.east.defenses["air"] = 0
    thick.east.defenses["air"] = 90
    strike(thin, weapon="drone_swarm", target="civilian")
    strike(thick, weapon="drone_swarm", target="civilian")
    assert thick.east.casualties < thin.east.casualties


def test_casualties_scale_with_what_actually_landed():
    assert casualties_from(20, "civilian", "drone_swarm") > casualties_from(
        5, "civilian", "drone_swarm"
    )
