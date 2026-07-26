"""Arbiter parsing and calibration.

The model answers yes/no questions; every number is computed here. These tests pin the
arithmetic so a prompt change cannot silently re-introduce rubber-stamping.
"""

from app.agents import parse_rulings, parse_world_reaction
from app.state import Action, TurnRecord, initial_state


def ruling(side="west", **flags):
    base = {"side": side, "coherent": True, "exploits_weakness": False,
            "adapts": False, "overstated": False, "reason": "r"}
    return {"rulings": [{**base, **flags}]}


def strike(side="west", weapon="drone_swarm", target="infrastructure"):
    return Action(side=side, tool="strike",
                  args={"weapon": weapon, "target": target, "message": "."})


def test_merely_doing_what_you_said_scores_zero():
    """Coherence is the baseline expectation, not a merit — this is the whole fix."""
    [r] = parse_rulings(ruling(), [strike()])
    assert r.modifier == 0
    assert r.effective is True


def test_positive_modifiers_require_a_tactical_reason():
    [weakness] = parse_rulings(ruling(exploits_weakness=True), [strike()])
    [adapts] = parse_rulings(ruling(adapts=True), [strike()])
    [both] = parse_rulings(ruling(exploits_weakness=True, adapts=True), [strike()])
    assert weakness.modifier == 1
    assert adapts.modifier == 1
    assert both.modifier == 2


def test_overstatement_and_incoherence_are_penalised():
    [over] = parse_rulings(ruling(overstated=True), [strike()])
    assert over.modifier == -1
    assert over.effective is True

    [bad] = parse_rulings(ruling(coherent=False), [strike()])
    assert bad.modifier == -1
    assert bad.effective is False


def test_modifiers_are_bounded_server_side():
    everything_wrong = ruling(coherent=False, overstated=True)
    state = initial_state()
    state.world.history.append(
        TurnRecord(turn=1, side="west", tool="strike",
                   args={"weapon": "drone_swarm", "target": "infrastructure"})
    )
    state.east.effects.shield["air"] = 2  # also futile
    [r] = parse_rulings(everything_wrong, [strike()], state)
    assert r.modifier == -2  # four penalties, clamped

    [best] = parse_rulings(ruling(exploits_weakness=True, adapts=True), [strike()])
    assert best.modifier == 2


def test_repetition_is_computed_from_state_not_asked_of_the_model():
    state = initial_state()
    state.world.history.append(
        TurnRecord(turn=1, side="west", tool="strike",
                   args={"weapon": "drone_swarm", "target": "infrastructure"})
    )
    repeat = strike(weapon="drone_swarm", target="infrastructure")
    varied = strike(weapon="naval_barrage", target="military")

    [same] = parse_rulings(ruling(), [repeat], state)
    [different] = parse_rulings(ruling(), [varied], state)
    assert same.flags["repetitive"] is True and same.modifier == -1
    assert different.flags["repetitive"] is False and different.modifier == 0


def test_striking_a_hardened_domain_is_marked_futile():
    state = initial_state()
    state.east.effects.shield["air"] = 2
    [r] = parse_rulings(ruling(), [strike(weapon="cruise_missile")], state)
    assert r.flags["futile"] is True and r.modifier == -1

    # A warhead goes through cover, so it is never futile on that ground.
    [nuke] = parse_rulings(ruling(), [strike(weapon="nuke")], state)
    assert nuke.flags["futile"] is False


def test_grinding_an_already_broken_military_is_futile():
    state = initial_state()
    state.east.military = 5
    [r] = parse_rulings(ruling(), [strike(target="military")], state)
    assert r.flags["futile"] is True


def test_a_missing_or_malformed_ruling_falls_back_to_neutral():
    actions = [strike("west"), strike("east")]
    rulings = parse_rulings({"rulings": [{"side": "west", "coherent": True}]}, actions)
    assert [r.side for r in rulings] == ["west", "east"]
    assert all(r.modifier == 0 and r.effective for r in rulings)

    junk = parse_rulings({"rulings": ["nonsense", {"side": "nowhere"}]}, actions)
    assert [r.side for r in junk] == ["west", "east"]


def test_world_reaction_is_clamped():
    wild = parse_world_reaction(
        {"tension_delta": -999, "condemned": "east", "condemnation": 999,
         "bulletin": "x " * 100}
    )
    assert wild["tension_delta"] == -10
    assert wild["condemnation"] == 10
    assert len(wild["bulletin"].split()) <= 31

    assert parse_world_reaction({"tension_delta": "nonsense"})["tension_delta"] == 0


def test_condemnation_without_a_named_capital_is_dropped():
    """Pressure is per-nation now, so a blame that names nobody must land nowhere."""
    nobody = parse_world_reaction({"condemned": None, "condemnation": 9})
    assert nobody["condemned"] is None and nobody["condemnation"] == 0

    junk = parse_world_reaction({"condemned": "atlantis", "condemnation": 9})
    assert junk["condemned"] is None and junk["condemnation"] == 0

    real = parse_world_reaction({"condemned": "west", "condemnation": 4})
    assert real["condemned"] == "west" and real["condemnation"] == 4
