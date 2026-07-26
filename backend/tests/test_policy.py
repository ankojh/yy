"""The scripted policy is the reference the balance is tuned against, so it is tested
like a player: does it reach for the right instrument when the condition is true?"""

import asyncio
import collections

from app import agents
from app.game import Game
from app.state import TurnRecord, initial_state


def choose(state, side="west"):
    agents.seed(0)
    return agents._mock_action(state, side, agents.legal_tools_for(state, side))


def test_capitulates_only_when_collapse_is_imminent():
    state = initial_state()
    assert choose(state).tool != "surrender"

    state.west.integrity = 10
    action = choose(state)
    assert action.tool == "surrender"
    assert action.args["acknowledge"] == "I ACCEPT DEFEAT"


def test_shores_up_a_cracking_home_front():
    state = initial_state()
    state.west.morale = 20
    assert choose(state).tool == "address_public"


def test_recovers_instead_of_striking_when_depleted():
    state = initial_state()
    state.west.military = 8
    state.west.strike_streak = 1
    assert choose(state).tool == "hold"


def test_does_not_strike_on_reflex_when_fatigued():
    state = initial_state()
    state.west.strike_streak = 1
    assert choose(state).tool != "strike"


def test_strikes_when_rested_and_armed():
    state = initial_state()
    assert choose(state).tool == "strike"


def test_fortifies_the_domain_it_is_actually_being_hit_through():
    state = initial_state()
    state.west.strike_streak = 1          # cannot strike well
    state.west.cooldowns = {"blockade": 2, "intl_appeal": 2}
    state.west.morale = 50
    for turn in (1, 2):
        state.world.history.append(
            TurnRecord(turn=turn, side="east", tool="strike",
                       args={"weapon": "cyber_strike", "target": "infrastructure"})
        )
    action = choose(state)
    assert action.tool == "fortify"
    assert action.args["domain"] == "cyber"


def test_ignores_a_single_probe_as_a_pattern():
    """One hit is not a pattern; the policy must not fortify against noise."""
    state = initial_state()
    state.west.strike_streak = 1
    state.west.cooldowns = {"blockade": 2, "intl_appeal": 2}
    state.world.history.append(
        TurnRecord(turn=1, side="east", tool="strike",
                   args={"weapon": "cyber_strike", "target": "infrastructure"})
    )
    assert choose(state).tool != "fortify"


def test_blockades_when_it_cannot_strike():
    state = initial_state()
    state.west.strike_streak = 1
    assert choose(state).tool == "blockade"


def test_takes_a_real_grievance_to_the_council_when_standing_bleeds():
    state = initial_state()
    state.west.strike_streak = 1
    state.west.standing = 30
    state.world.history.append(
        TurnRecord(turn=1, side="east", tool="strike",
                   args={"weapon": "drone_swarm", "target": "civilian"})
    )
    assert choose(state).tool == "intl_appeal"


def test_does_not_petition_the_council_without_a_case():
    state = initial_state()
    state.west.strike_streak = 1
    state.west.standing = 30
    state.west.cooldowns = {"blockade": 2}
    assert choose(state).tool != "intl_appeal"


def test_avoids_a_domain_the_enemy_has_hardened():
    state = initial_state()
    state.east.effects.shield["air"] = 2
    state.west.arsenal = {"cruise_missile": 2, "naval_barrage": 2}
    action = choose(state)
    assert action.tool == "strike"
    assert action.args["weapon"] == "naval_barrage"  # air is covered


def test_never_offered_an_illegal_tool():
    """Whatever the policy picks must be in the legal set — no exceptions, any state."""
    for seed in range(12):
        agents.seed(seed)
        state = initial_state()
        state.west.military = seed * 7 % 60
        state.west.strike_streak = seed % 3
        state.west.integrity = 100 - seed * 6
        legal = agents.legal_tools_for(state, "west")
        assert agents._mock_action(state, "west", legal).tool in legal


def test_seeded_matches_are_reproducible():
    async def play(seed):
        game = Game(seed=seed, write_log=False)
        await game.reset()
        await game.ignite(["reef"])
        await game.run()
        return [
            (e.payload["side"], e.payload["tool"]) for e in game.log if e.type == "message"
        ], game.state.world.outcome

    first = asyncio.run(play(7))
    second = asyncio.run(play(7))
    assert first == second


def test_the_policy_exercises_every_mechanic_across_a_batch():
    async def play(seed):
        game = Game(seed=seed, write_log=False)
        await game.reset()
        await game.ignite(["blackout"])
        await game.run()
        return collections.Counter(
            e.payload["tool"] for e in game.log if e.type == "message"
        )

    seen = collections.Counter()
    for seed in range(10):
        seen += asyncio.run(play(seed))

    for tool in ("strike", "blockade", "fortify", "address_public",
                 "intl_appeal", "propaganda", "hold"):
        assert seen[tool] > 0, f"{tool} never chosen across 10 matches: {dict(seen)}"

    # Strike stays the backbone of the war without being the whole war.
    share = seen["strike"] / sum(seen.values())
    assert 0.25 < share < 0.7, f"strike share {share:.0%}: {dict(seen)}"


def test_the_policy_spends_the_magazine_it_actually_has():
    """Each nation has to fight with its own arsenal, not with whatever is heaviest.

    Korsav's deep drone and naval magazines went entirely unfired for a whole balance
    revision because the policy always reached for the biggest warhead it could afford.
    An asymmetry nobody uses is a paragraph in a config file.
    """
    async def play(seed):
        game = Game(seed=seed, write_log=False)
        await game.reset()
        await game.ignite(["reef"])
        await game.run()
        return [
            (e.payload["side"], e.payload["args"].get("weapon"))
            for e in game.log
            if e.type == "message" and e.payload["tool"] == "strike"
        ]

    fired = collections.defaultdict(collections.Counter)
    for seed in range(10):
        for side, weapon in asyncio.run(play(seed)):
            fired[side][weapon] += 1

    for side in ("west", "east"):
        assert len(fired[side]) >= 2, f"{side} only ever fired {dict(fired[side])}"
    # And they must not converge on the same war. Korsav is the naval and drone power;
    # Aurelia's weight is in precision air and cyber.
    assert fired["east"]["naval_barrage"] > fired["west"]["naval_barrage"]
    assert fired["west"]["cyber_strike"] > fired["east"]["cyber_strike"]
    assert fired["east"].most_common(1)[0][0] != fired["west"].most_common(1)[0][0]
