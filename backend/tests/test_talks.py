"""The negotiating table.

The mechanic exists to give a losing government something to do besides lose slowly, and
it is deliberately abusable: a ceasefire refits both armies, so asking for one is worth a
turn whether or not you mean a word of it. These tests hold that shape in place — that
you can only ask from a losing position, that nothing can be fired while the table is
open, that a concession costs unrest the moment it is said aloud, and that a round in
which nobody moves is what actually ends most negotiations.
"""

import pytest

from app.agents import legal_tools_for
from app.engine import (
    TALKS_DEADLOCK_LIMIT,
    TALKS_REOPEN_COOLDOWN,
    apply_action,
    apply_upkeep,
    is_settlement,
    may_open_talks,
    resolve_talks,
    settled_articles,
)
from app.lore import ARTICLES, CORE_ARTICLES, SETTLEMENT_MINIMUM
from app.state import Action, initial_state


def act(side, tool, **args):
    return Action(side=side, tool=tool, args={**args, "message": "."})


def at_war(losing=None):
    """A live war, optionally with one side hurt badly enough to want a table."""
    state = initial_state()
    state.world.phase = "conflict"
    if losing:
        state.nation(losing).integrity = 40
    return state


def open_the_table(state, opener="west"):
    apply_action(state, act(opener, "open_talks"), None)
    return state.world.talks


# ---------------------------------------------------------------- getting to the table


def test_a_healthy_nation_cannot_ask_for_terms():
    """Suing for peace from a winning position is not a move; it is a bug report."""
    state = at_war()
    assert not may_open_talks(state, "west")
    assert "open_talks" not in legal_tools_for(state, "west")


def test_a_losing_nation_can():
    state = at_war(losing="west")
    assert may_open_talks(state, "west")
    assert "open_talks" in legal_tools_for(state, "west")
    # ...and the other side, which is doing fine, still cannot.
    assert not may_open_talks(state, "east")


@pytest.mark.parametrize(
    "field, value",
    [("integrity", 40), ("morale", 30), ("standing", 30), ("unrest", 70), ("budget", 5)],
)
def test_every_kind_of_losing_opens_the_table(field, value):
    """Not only bombardment. A bankrupt or a boiling country wants terms too."""
    state = at_war()
    setattr(state.west, field, value)
    assert may_open_talks(state, "west")


def test_nobody_may_ask_before_the_first_shot():
    state = initial_state()          # still in briefing
    state.west.integrity = 20
    assert not may_open_talks(state, "west")


# ---------------------------------------------------------------- the ceasefire


def test_the_guns_are_withdrawn_not_merely_discouraged():
    """A ceasefire a commander can break by choosing to is not a ceasefire."""
    state = at_war(losing="west")
    open_the_table(state)
    for side in ("west", "east"):
        legal = legal_tools_for(state, side)
        assert "strike" not in legal
        assert "blockade" not in legal
        assert "table_terms" in legal
        assert "walk_out" in legal


def test_the_table_tools_do_not_exist_when_there_is_no_table():
    legal = legal_tools_for(at_war(), "west")
    for tool in ("table_terms", "accept_terms", "walk_out"):
        assert tool not in legal


def test_a_ceasefire_lifts_the_blockade():
    """You cannot be closing a country's ports and negotiating with it at the same time."""
    state = at_war(losing="west")
    state.west.effects.blockaded = 3
    open_the_table(state)
    assert state.west.effects.blockaded == 0


def test_the_ceasefire_is_a_repair_dock_for_both_sides():
    """This is the buffer, and it has to be worth more than a turn of fighting or no
    rational commander would ever ask for one."""
    state = at_war(losing="west")
    state.west.military = 30
    state.east.military = 30
    state.west.unrest = 60
    open_the_table(state)
    before = {s: state.nation(s).military for s in ("west", "east")}
    unrest_before = state.west.unrest

    apply_upkeep(state)

    for side in ("west", "east"):
        assert state.nation(side).military > before[side] + 8
    assert state.west.unrest < unrest_before


def test_war_weariness_stops_while_the_guns_do():
    quiet = at_war(losing="west")
    loud = at_war(losing="west")
    for state in (quiet, loud):
        state.world.turn = 9
        state.west.unrest = 40
    open_the_table(quiet)

    apply_upkeep(quiet)
    apply_upkeep(loud)
    assert quiet.west.unrest < loud.west.unrest


# ---------------------------------------------------------------- the clauses


def test_a_demand_met_by_a_concession_is_an_agreement():
    state = at_war(losing="west")
    talks = open_the_table(state)
    apply_action(state, act("east", "table_terms", demand=["bellow_reef"]), None)
    apply_action(state, act("west", "table_terms", concede=["bellow_reef"]), None)
    assert settled_articles(state) == ["bellow_reef"]
    assert talks.stance("bellow_reef", "west") == "concede"


def test_two_demands_are_a_deadlock_not_an_agreement():
    state = at_war(losing="west")
    open_the_table(state)
    apply_action(state, act("east", "table_terms", demand=["bellow_reef"]), None)
    apply_action(state, act("west", "table_terms", demand=["bellow_reef"]), None)
    assert settled_articles(state) == []


def test_both_conceding_settles_it_too():
    """Neither capital can hold the reef, so it goes to joint development. That is a
    real outcome of a negotiation and should not read as a deadlock."""
    state = at_war(losing="west")
    open_the_table(state)
    apply_action(state, act("east", "table_terms", concede=["bellow_reef"]), None)
    apply_action(state, act("west", "table_terms", concede=["bellow_reef"]), None)
    assert settled_articles(state) == ["bellow_reef"]


def test_conceding_costs_unrest_the_moment_it_is_said_aloud():
    """The whole reason talks stall. A government whose streets are already full cannot
    afford the concession that would empty them."""
    state = at_war(losing="west")
    open_the_table(state)
    before = state.west.unrest
    apply_action(state, act("west", "table_terms", concede=["halcyon_apology"]), None)
    assert state.west.unrest > before


def test_demanding_costs_nothing():
    state = at_war(losing="west")
    open_the_table(state)
    before = state.west.unrest
    apply_action(state, act("west", "table_terms", demand=list(ARTICLES)), None)
    assert state.west.unrest == before


def test_the_same_concession_is_not_charged_twice():
    """Re-tabling a position you already hold is restating it, not paying for it again."""
    state = at_war(losing="west")
    open_the_table(state)
    apply_action(state, act("west", "table_terms", concede=["meridian_cable"]), None)
    once = state.west.unrest
    apply_action(state, act("west", "table_terms", concede=["meridian_cable"]), None)
    assert state.west.unrest == once


def test_a_clause_named_in_both_lists_is_a_demand():
    """A model will occasionally hand you both. Holding it is the safe reading, and it
    must not quietly charge unrest for a concession that was never made."""
    state = at_war(losing="west")
    talks = open_the_table(state)
    before = state.west.unrest
    apply_action(
        state, act("west", "table_terms", demand=["bellow_reef"], concede=["bellow_reef"]), None
    )
    assert talks.stance("bellow_reef", "west") == "demand"
    assert state.west.unrest == before


def test_a_comma_separated_string_is_accepted_as_a_list():
    """Schemas say array; models sometimes send 'a, b'. Losing a whole negotiating round
    to a JSON shape is not a mechanic."""
    state = at_war(losing="west")
    talks = open_the_table(state)
    apply_action(state, act("west", "table_terms", concede="meridian_cable, anvil_claims"), None)
    assert talks.stance("meridian_cable", "west") == "concede"
    assert talks.stance("anvil_claims", "west") == "concede"


def test_invented_clauses_are_ignored():
    state = at_war(losing="west")
    talks = open_the_table(state)
    apply_action(state, act("west", "table_terms", concede=["the_moon"]), None)
    assert "the_moon" not in talks.positions


# ---------------------------------------------------------------- signing, or not


def settle_enough(state, folding="west"):
    """Drive both sides to a position that completes a treaty."""
    holding = "east" if folding == "west" else "west"
    needed = list(CORE_ARTICLES)
    for key in ARTICLES:
        if len(needed) >= SETTLEMENT_MINIMUM:
            break
        if key not in needed:
            needed.append(key)
    apply_action(state, act(holding, "table_terms", demand=needed), None)
    apply_action(state, act(folding, "table_terms", concede=needed), None)
    return needed


def test_the_core_clauses_are_not_optional():
    """Three cheap clauses is not a peace. The boundary and the gas field are the war."""
    assert not is_settlement([k for k in ARTICLES if k not in CORE_ARTICLES][:3])
    assert is_settlement(list(CORE_ARTICLES) + [
        k for k in ARTICLES if k not in CORE_ARTICLES][:1])


def test_enough_agreed_clauses_end_the_war_with_no_loser():
    state = at_war(losing="west")
    open_the_table(state)
    settle_enough(state)
    resolve_talks(state)

    assert state.world.phase == "over"
    assert state.world.loser is None      # a settlement is the one ending nobody loses
    assert "Strait Accord" in state.world.outcome
    assert not state.world.talks.open


def test_the_settlement_names_who_gave_up_more():
    state = at_war(losing="west")
    open_the_table(state)
    settle_enough(state, folding="west")
    resolve_talks(state)
    assert "Aurelia gave up more" in state.world.outcome


def test_accepting_their_terms_concedes_everything_they_demanded():
    state = at_war(losing="west")
    talks = open_the_table(state)
    apply_action(state, act("east", "table_terms", demand=["kestrel_line", "bellow_reef"]), None)
    apply_action(state, act("west", "accept_terms"), None)
    assert talks.stance("kestrel_line", "west") == "concede"
    assert talks.stance("bellow_reef", "west") == "concede"


def test_accepting_an_offer_nobody_made_does_nothing():
    state = at_war(losing="west")
    open_the_table(state)
    before = state.west.unrest
    _, notes = apply_action(state, act("west", "accept_terms"), None)
    assert state.west.unrest == before
    assert any("nobody has actually offered" in n for n in notes)


def test_the_opening_round_is_not_charged_as_a_deadlock():
    """Both cabinets chose this turn's move before the table existed. Docking them for
    not having tabled terms at it would end most negotiations on turn one."""
    state = at_war(losing="west")
    talks = open_the_table(state)
    resolve_talks(state)
    assert talks.deadlock == 0
    assert talks.open


def test_two_rounds_agreeing_nothing_collapses_the_talks():
    state = at_war(losing="west")
    talks = open_the_table(state)
    for _ in range(TALKS_DEADLOCK_LIMIT):
        # Both hold out on the same clause: a position, and no movement.
        apply_action(state, act("west", "table_terms", demand=["bellow_reef"]), None)
        apply_action(state, act("east", "table_terms", demand=["bellow_reef"]), None)
        resolve_talks(state)

    assert not talks.open
    assert state.world.phase == "conflict"
    assert state.world.talks_cooldown == TALKS_REOPEN_COOLDOWN


def test_agreement_resets_the_deadlock_clock():
    state = at_war(losing="west")
    talks = open_the_table(state)
    apply_action(state, act("west", "table_terms", demand=["bellow_reef"]), None)
    apply_action(state, act("east", "table_terms", demand=["bellow_reef"]), None)
    resolve_talks(state)
    assert talks.deadlock == 1

    apply_action(state, act("west", "table_terms", concede=["meridian_cable"]), None)
    apply_action(state, act("east", "table_terms", demand=["meridian_cable"]), None)
    resolve_talks(state)
    assert talks.deadlock == 0
    assert talks.open


def test_walking_out_is_blamed_on_whoever_left_the_room():
    state = at_war(losing="west")
    open_the_table(state)
    pressure = state.east.intl_pressure
    apply_action(state, act("east", "walk_out"), None)

    assert not state.world.talks.open
    assert state.east.intl_pressure > pressure
    assert state.world.talks_cooldown == TALKS_REOPEN_COOLDOWN


def test_the_table_cannot_be_reopened_immediately():
    state = at_war(losing="west")
    open_the_table(state)
    apply_action(state, act("east", "walk_out"), None)
    state.west.integrity = 20                       # losing worse than ever
    assert not may_open_talks(state, "west")

    for _ in range(TALKS_REOPEN_COOLDOWN):
        apply_upkeep(state)
    assert may_open_talks(state, "west")


def test_tabling_terms_with_no_table_is_a_note_not_a_crash():
    state = at_war()
    _, notes = apply_action(state, act("west", "table_terms", concede=["bellow_reef"]), None)
    assert notes and "no table" in notes[0]
    assert not settled_articles(state)
