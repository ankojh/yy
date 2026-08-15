"""Deterministic mechanics.

The split that matters: this file owns every number. The arbiter LLM only ever returns a
bounded modifier and a judgment call, which get applied here. A model can never invent a
state change, so a hallucination costs you flavor, not integrity of the simulation.

Infrastructure reaching zero; public unrest reaching one hundred and taking the
government with it; somebody signing a capitulation; or both capitals signing a
settlement at the table.
"""

from typing import Dict, List, Optional, Tuple

from .config import settings
from .lore import ARTICLES, CORE_ARTICLES, SETTLEMENT_MINIMUM
from .state import Action, Atrocity, Event, GameState, Nation, Ruling, clamp
from .tools import (
    BLOCKADE_DEATHS,
    TOOL_META,
    WEAPONS,
    casualties_from,
    fatigue_power,
    intercepted_fraction,
    intercepted_rounds,
    place_struck,
    strike_cost,
    strike_pressure,
    strike_price,
    usd,
)

Delta = Dict[str, object]

# How long each persistent effect runs, counted in upkeeps.
SHIELD_TURNS = 2
BLOCKADE_TURNS = 3
SANCTION_TURNS = 3
SPIN_TURNS = 3
# Hardened cover was letting a third of the salvo through, which alongside a defence
# number that barely mattered meant nobody could see that fortifying had done anything.
# A fifth gets through now, and the fortify also raises the standing defence that decides
# how much of every *later* salvo is shot down.
SHIELD_ABSORPTION = 0.2
FORTIFY_GAIN = 18             # permanent defence added to the hardened domain
HOLD_DEFENCE_GAIN = 6         # every domain, for standing down
MORALE_BUFFER_GAIN = 14
MORALE_BUFFER_CAP = 26

# ------------------------------------------------------------------- the table
#
# Negotiation is a buffer, and it is meant to be abusable. A losing government can buy
# a ceasefire and spend it refitting; the cost is that the other side gets the same
# turns, the world watches who leaves the room, and the clauses cost unrest to concede.
# Most rounds of talks should end in nothing. That is what makes the one that doesn't
# worth watching.
TALKS_MAX_ROUNDS = 4          # rounds in a single sitting before it lapses on its own
TALKS_DEADLOCK_LIMIT = 2      # consecutive rounds agreeing nothing, then it collapses
TALKS_REOPEN_COOLDOWN = 3     # turns before anyone may ask again
TALKS_TOTAL_CAP = 6           # rounds of negotiation a whole war may spend
CEASEFIRE_REFIT = 9           # extra capacity per side per round at the table
CEASEFIRE_CREDITS = 7
CEASEFIRE_CALM = 6            # unrest the silence takes off the streets, per round

# ------------------------------------------------------------------- the economy
#
# Output is an index; income is a slice of it, taxed by how isolated you are. A country
# at zero international pressure with intact infrastructure funds roughly one heavy
# sortie a turn. A pariah under blockade funds nothing, which is the point: bankruptcy
# is a way to lose a war without ever losing a battle.
INCOME_RATE = 0.28            # $B of revenue per point of output, per turn
TRADE_FLOOR = 0.15            # even a pariah smuggles something
BLOCKADE_TRADE_CUT = 0.62     # multiplier on income while the sea lanes are shut
BUDGET_CEILING = 999
BASE_UPKEEP = 4               # standing costs of being a country at war


def _bump(
    nation: Nation, field: str, amount: float, out: List[Delta], high: int = 100
) -> None:
    if not amount:
        return
    before = getattr(nation, field)
    after = clamp(before + amount, 0, high)
    if after == before:
        return
    setattr(nation, field, after)
    out.append({"side": nation.side, "field": field, "delta": after - before, "value": after})


def _spend(nation: Nation, price: int, out: List[Delta]) -> None:
    """Pay a bill out of the treasury. Legality already guaranteed it is affordable."""
    _bump(nation, "budget", -price, out, high=BUDGET_CEILING)


def _drift(
    nation: Nation, field: str, amount: float, out: List[Delta], high: int = 100
) -> None:
    """Apply a change, carrying the fraction that does not yet amount to a whole point.

    The meters are integers. Without a carry, a sixth of a point of war weariness per
    turn is not "slow", it is zero — and a nation whose multipliers happened to land
    below the rounding threshold became permanently immune to the effect instead of
    merely resistant to it.
    """
    if not amount:
        return
    total = nation.effects.carry.get(field, 0.0) + amount
    whole = int(total)  # truncates toward zero, so the sign takes care of itself
    nation.effects.carry[field] = total - whole
    if whole:
        _bump(nation, field, whole, out, high)


def _hit_morale(nation: Nation, amount: float, out: List[Delta]) -> None:
    """Morale damage passes through the buffer a public address banked, if any."""
    if amount <= 0:
        return
    absorbed = min(nation.effects.morale_buffer, amount)
    nation.effects.morale_buffer = int(round(nation.effects.morale_buffer - absorbed))
    remaining = amount - absorbed
    if remaining > 0:
        _bump(nation, "morale", -remaining, out)


def _stir(nation: Nation, amount: float, out: List[Delta]) -> None:
    """Public unrest filtered through the island's political temperament."""
    if amount <= 0:
        return
    amount *= nation.traits.unrest_sensitivity
    _drift(nation, "unrest", amount, out)


def _isolate(nation: Nation, amount: float, out: List[Delta]) -> None:
    """International pressure, filtered through how much benefit of the doubt you get.

    The two islands receive different degrees of international benefit of the doubt.
    """
    if amount <= 0:
        return
    amount *= nation.traits.intl_sensitivity
    _drift(nation, "intl_pressure", amount, out)


def _kill(nation: Nation, dead: int, out: List[Delta]) -> None:
    """Add to a country's butcher's bill.

    Casualties are not a meter and are not clamped to a hundred — they are a count, and
    the whole point of showing them is that they go somewhere the meters cannot.
    """
    if dead <= 0:
        return
    nation.casualties += dead
    out.append(
        {"side": nation.side, "field": "casualties", "delta": dead, "value": nation.casualties}
    )


def strike_interception(state: GameState, side: str, weapon: str) -> Dict[str, object]:
    """What the defender's batteries do to this sortie, before anything is applied.

    Pure function of the state, so the renderer and the damage calculation can both ask
    for it and get the same answer. That is the point: the number of objects that die in
    the sky on screen is the number the engine actually stopped, not a second roll of
    the dice that happens to look similar.
    """
    spec = WEAPONS.get(weapon)
    if not spec:
        return {}
    foe = state.foe(side)
    domain = str(spec["domain"])
    cover = min(100, int(foe.defenses.get(domain, 0)))
    hardened = weapon != "nuke" and foe.effects.shield.get(domain, 0) > 0
    return {
        "domain": domain,
        "cover": cover,
        "salvo": int(spec["salvo"]),
        "stopped": intercepted_rounds(cover, weapon),
        "fraction": round(intercepted_fraction(cover, weapon), 3),
        "hardened": hardened,
    }


def _scale(ruling: Optional[Ruling]) -> float:
    """Turn the arbiter's judgment into a magnitude multiplier."""
    if ruling is None:
        return 1.0
    if not ruling.effective:
        return 0.3
    return 1.0 + 0.25 * max(-2, min(2, ruling.modifier))


def apply_action(
    state: GameState, action: Action, ruling: Optional[Ruling]
) -> Tuple[List[Delta], List[str]]:
    """Apply one declared action. Returns state deltas and any world-level notes."""
    me = state.nation(action.side)
    foe = state.foe(action.side)
    world = state.world
    k = _scale(ruling)
    deltas: List[Delta] = []
    notes: List[str] = []

    meta = TOOL_META.get(action.tool, {})
    if meta.get("cost", 0):
        _bump(me, "military", -meta["cost"], deltas)
    if meta.get("price", 0):
        _spend(me, meta["price"], deltas)
    if meta.get("cooldown", 0):
        me.cooldowns[action.tool] = meta["cooldown"] + 1  # +1: upkeep ticks it down this turn

    tool = action.tool

    # Strike fatigue. Read the streak *before* this action mutates it, then reset it
    # for anything that is not another strike — that reset is what makes the other
    # tools worth reaching for.
    streak = me.strike_streak
    me.strike_streak = streak + 1 if tool == "strike" else 0

    if tool == "strike":
        weapon = str(action.args.get("weapon", "drone_swarm"))
        spec = WEAPONS.get(weapon)
        if not spec or me.arsenal.get(weapon, 0) <= 0:
            notes.append(f"{me.name} reached for {weapon.replace('_', ' ')} and found the racks empty")
            return deltas, notes

        sanctioned = me.effects.sanctioned > 0
        blockaded = me.effects.blockaded > 0
        price = strike_price(weapon, sanctioned, blockaded)
        if price > me.budget:
            notes.append(
                f"{me.name}'s treasury cannot find {usd(price)} for the sortie; "
                "the aircraft stay on the apron."
            )
            return deltas, notes

        me.arsenal[weapon] -= 1
        _spend(me, price, deltas)
        _bump(me, "military", -strike_cost(weapon, streak, sanctioned, blockaded), deltas)
        target = str(action.args.get("target", "military"))
        domain = str(spec["domain"])
        nuclear = weapon == "nuke"

        # What the batteries stopped. Computed once, here, from a pure function the
        # renderer also calls — so the rounds that die on screen are the rounds that
        # died in the arithmetic.
        shot = strike_interception(state, action.side, weapon)
        damage = spec["damage"] * k * (1 - float(shot["fraction"]))
        if shot["stopped"]:
            notes.append(
                f"{foe.name}'s {domain} defences destroy {shot['stopped']} of "
                f"{shot['salvo']} inbound."
            )
        # Tired crews fly worse sorties.
        damage *= fatigue_power(streak)
        if streak >= 1:
            notes.append(f"{me.name}'s crews fly a second sortie in as many days, and it shows.")

        # Hardened cover is the real payoff of fortify: it eats most of one strike and
        # is spent doing so. A warhead goes straight through it.
        if shot["hardened"]:
            damage *= SHIELD_ABSORPTION
            foe.effects.shield[domain] = 0
            notes.append(
                f"{foe.name}'s hardened {domain} cover absorbs the worst of it, and is spent."
            )

        # What the world makes of it, before anything else. Heavier ordnance on softer
        # targets isolates you faster — this is the whole pressure model in one line.
        _isolate(me, strike_pressure(weapon, target) * (1.0 + 0.25 * (k - 1)), deltas)

        # Who it came down on. The engine names the *kind* of place; the map, which is
        # the only thing that knows where the towns are, names the town.
        place, protected = place_struck(
            target,
            world.turn * 31 + len(world.atrocities) * 11 + (5 if action.side == "east" else 0),
        )
        dead = casualties_from(damage, target, weapon)

        if nuclear:
            _bump(foe, "integrity", -damage, deltas)
            _stir(foe, 26, deltas)
            # Even a state broadcaster cannot sell this one at home.
            _bump(me, "unrest", 18, deltas)
            world.tension = 100
            place, protected = "the capital", True
            notes.append(
                f"{me.name} has detonated a nuclear weapon over {foe.name}. "
                "There is no going back from this."
            )
        else:
            if target == "military":
                # Attrition, not annihilation. Military regenerates, so hitting it can
                # only ever delay the enemy — it must not also bankrupt them in one turn.
                _bump(foe, "military", -damage * 0.55, deltas)
                _stir(foe, 1.5, deltas)
            elif target == "infrastructure":
                _bump(foe, "integrity", -damage, deltas)
                _stir(foe, 4, deltas)
            else:  # civilian
                _bump(foe, "integrity", -damage * 0.5, deltas)
                # Their streets fill with grief; yours fill with people who did not want
                # this done in their name.
                _stir(foe, 12, deltas)
                _stir(me, 6, deltas)
                notes.append(f"{me.name} struck civilian targets — the world is watching")

            world.tension = clamp(world.tension + (12 if target == "civilian" else 8))

        _kill(foe, dead, deltas)
        # A red cross or a school roof is a different order of thing from a substation,
        # and the difference is charged where it belongs: to the capital that fired.
        if protected and dead > 0:
            _isolate(me, 7 if not nuclear else 20, deltas)
            _stir(foe, 2, deltas)
        if dead > 0 and (protected or target == "civilian" or nuclear):
            world.atrocities.append(
                Atrocity(
                    turn=world.turn, victim=foe.side, attacker=me.side,
                    place=place, dead=dead, weapon=weapon, protected=protected,
                )
            )
            notes.append(
                f"{place.capitalize()} in {foe.name} is destroyed. {dead:,} dead."
                if protected
                else f"{dead:,} dead in {foe.name}."
            )

        foe.defenses[domain] = clamp(foe.defenses.get(domain, 0) - 6)

    elif tool == "blockade":
        # The point is the next three turns, not this one. Upkeep does the work.
        foe.effects.blockaded = BLOCKADE_TURNS
        _bump(foe, "military", -5 * k, deltas)
        _bump(foe, "budget", -6 * k, deltas, high=BUDGET_CEILING)
        _stir(foe, 5, deltas)
        _isolate(me, 5, deltas)
        world.tension = clamp(world.tension + 6)
        notes.append(f"{me.name} closes the sea lanes around {foe.name}.")

    elif tool == "fortify":
        domain = str(action.args.get("domain", "air"))
        me.effects.shield[domain] = SHIELD_TURNS
        before = me.defenses.get(domain, 0)
        me.defenses[domain] = clamp(before + FORTIFY_GAIN * k)
        # Said out loud, because the whole complaint about fortify was that you could
        # not tell whether it had done anything.
        notes.append(
            f"{me.name} hardens its {domain} defences — cover {before} → "
            f"{me.defenses[domain]}, and the next salvo through it is mostly absorbed."
        )

    elif tool == "intl_appeal":
        # Credibility is the arbiter's call — a baseless accusation barely moves the needle.
        # The point of an appeal is the *transfer*: pressure comes off you and lands on
        # them. It is the only instrument that lowers your own isolation.
        _bump(me, "intl_pressure", -7 * k, deltas)
        if ruling is None or ruling.effective:
            _isolate(foe, 12 * k, deltas)
            foe.effects.sanctioned = SANCTION_TURNS
            notes.append(f"The council votes sanctions against {foe.name}.")
        world.tension = clamp(world.tension - 3)

    elif tool == "address_public":
        _bump(me, "unrest", -10 * k, deltas)

    elif tool == "propaganda":
        # Narrative warfare is finite ammunition aimed at the enemy's public. The
        # target's own political temperament determines how far the story travels.
        if me.arsenal.get("narrative", 0) <= 0:
            notes.append(f"{me.name} has exhausted its influence network.")
            return deltas, notes
        me.arsenal["narrative"] -= 1
        credible = ruling is None or ruling.effective
        if credible:
            _stir(foe, 14 * k, deltas)
            notes.append(f"{me.name}'s narrative warfare operation spreads through {foe.name}.")
        else:
            _stir(me, 7, deltas)
            notes.append(
                f"{me.name}'s narrative warfare operation is exposed and backfires at home."
            )
        _isolate(me, 6, deltas)

    elif tool == "open_talks":
        talks = world.talks
        talks.open = True
        talks.round = 0
        talks.opened_by = me.side
        talks.positions = {}
        talks.settled = []
        talks.deadlock = 0
        talks.transcript = [f"{me.name} asks for terms."]
        # A ceasefire opens the sea lanes. You cannot blockade a country you are
        # negotiating with and expect anyone to call it a ceasefire.
        me.effects.blockaded = foe.effects.blockaded = 0
        # Asking costs nothing in dollars and a great deal in every other currency: your own
        # people hear a government that thinks it is losing, and it is.
        _stir(me, 2, deltas)
        _bump(me, "intl_pressure", -6, deltas)
        world.tension = clamp(world.tension - 12)
        notes.append(
            f"{me.name} sues for terms. The guns fall silent across the strait while "
            f"{foe.name} decides whether to sit down."
        )

    elif tool == "table_terms":
        talks = world.talks
        if not talks.open:
            notes.append(f"{me.name} tables terms, and there is no table to put them on.")
        else:
            demand = [a for a in _clauses(action.args.get("demand")) if a in ARTICLES]
            concede = [a for a in _clauses(action.args.get("concede")) if a in ARTICLES]
            moved: List[str] = []
            for article in demand:
                talks.table(article, me.side, "demand")
            for article in concede:
                if article in demand or talks.stance(article, me.side) == "concede":
                    continue
                talks.table(article, me.side, "concede")
                moved.append(article)
                # Paid the moment it is said aloud, not when it is signed. This is the
                # whole reason talks stall: a government whose streets are already full
                # cannot afford the concession that would empty them.
                _stir(me, ARTICLES[article]["cost"][me.side], deltas)
            if moved:
                titles = ", ".join(ARTICLES[a]["title"] for a in moved)
                talks.transcript.append(f"{me.name} gives ground on {titles}.")
                notes.append(f"{me.name} concedes {titles} at the table.")
            if demand:
                talks.transcript.append(
                    f"{me.name} holds out for "
                    + ", ".join(ARTICLES[a]["title"] for a in demand)
                    + "."
                )

    elif tool == "accept_terms":
        talks = world.talks
        wanted = [
            a for a in ARTICLES
            if talks.open
            and a not in talks.settled
            and talks.stance(a, foe.side) == "demand"
            and talks.stance(a, me.side) != "concede"
        ]
        if not wanted:
            notes.append(f"{me.name} accepts terms nobody has actually offered.")
        else:
            for article in wanted:
                talks.table(article, me.side, "concede")
                _stir(me, ARTICLES[article]["cost"][me.side], deltas)
            titles = ", ".join(ARTICLES[a]["title"] for a in wanted)
            talks.transcript.append(f"{me.name} accepts: {titles}.")
            notes.append(f"{me.name} takes {foe.name}'s terms on {titles}.")

    elif tool == "walk_out":
        if not world.talks.open:
            notes.append(f"{me.name} walks out of a room it was not in.")
        else:
            world.talks.transcript.append(f"{me.name} leaves the table.")
            deltas.extend(collapse_talks(state, blame=me.side))
            notes.append(
                f"{me.name} walks out. The ceasefire lapses and the world notes who ended it."
            )

    elif tool == "surrender":
        # A terminal action has no magnitude, so an "ineffective" ruling used to do
        # nothing at all — a commander once capitulated while its own statement read
        # "we will not surrender", and the war ended on it. The arbiter can veto now.
        if not valid_surrender(action, ruling):
            notes.append(
                f"{me.name} moved to capitulate, then faltered — the order was never signed."
            )
        elif world.phase == "over" and world.loser == foe.side:
            # The enemy capitulated in the same breath. Nobody accepts anybody's sword.
            world.loser = None
            world.outcome = (
                f"{me.name} and {foe.name} capitulate in the same hour. "
                "Two governments fall and no one is left to accept a surrender."
            )
            notes.append("Both capitals surrender simultaneously.")
        else:
            world.phase = "over"
            world.loser = me.side
            world.outcome = f"{me.name} surrenders unconditionally. {foe.name} has won the war."
            notes.append(f"{me.name} has laid down its arms.")

    elif tool == "hold":
        # Has to be a real option, not a wasted turn, or fatigue just becomes a tax.
        _bump(me, "military", 12 * me.traits.industry, deltas)
        for domain in me.defenses:
            me.defenses[domain] = clamp(me.defenses[domain] + HOLD_DEFENCE_GAIN)
        _stir(me, 3, deltas)
        # Refitting also lets everything else come back sooner.
        for name in list(me.cooldowns):
            me.cooldowns[name] -= 1
            if me.cooldowns[name] <= 0:
                del me.cooldowns[name]
        world.tension = clamp(world.tension - 2)

    return deltas, notes


# --------------------------------------------------------------------------- the table


def _clauses(raw: object) -> List[str]:
    """A model will hand you a list, a comma string, or one bare id. Take all three."""
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def settled_articles(state: GameState) -> List[str]:
    """Clauses whose two positions are compatible.

    One side demanding and the other conceding is an agreement. Both conceding is also
    an agreement — that is the reef going to joint development because neither capital
    could hold it. Both demanding is the deadlock, and silence is not agreement.
    """
    talks = state.world.talks
    out = []
    for key in ARTICLES:
        west, east = talks.stance(key, "west"), talks.stance(key, "east")
        pair = {west, east}
        if pair == {"demand", "concede"} or pair == {"concede"}:
            out.append(key)
    return out


def is_settlement(settled: List[str]) -> bool:
    """Enough signed to end a war. Both core clauses, and one more that somebody paid for."""
    return all(c in settled for c in CORE_ARTICLES) and len(settled) >= SETTLEMENT_MINIMUM


def collapse_talks(state: GameState, blame: Optional[str] = None) -> List[Delta]:
    """Shut the table. The positions survive for the record; the ceasefire does not."""
    world = state.world
    deltas: List[Delta] = []
    world.talks.open = False
    world.talks_cooldown = TALKS_REOPEN_COOLDOWN
    world.tension = clamp(world.tension + 18)
    if blame:
        _isolate(state.nation(blame), 9, deltas)
    for nation in (state.west, state.east):
        # A country that was told peace was close and then was not tells its government so.
        _stir(nation, 3, deltas)
    return deltas


def resolve_talks(state: GameState) -> Tuple[List[Delta], List[str]]:
    """Close one round at the table: count what was agreed, then settle, stall or break.

    Called once per turn after both sides have spoken, because a clause is agreed by two
    positions and only one of them exists halfway through the turn.
    """
    world = state.world
    talks = world.talks
    if not talks.open or world.phase == "over":
        return [], []

    deltas: List[Delta] = []
    notes: List[str] = []
    talks.round += 1
    world.talks_held += 1

    if not talks.positions:
        # The turn the guns stopped. Both cabinets decided this turn's move before there
        # was a table, so charging either of them a deadlock for not having tabled terms
        # at a table that did not exist would end most negotiations before they began.
        notes.append("The ceasefire holds. Neither capital has tabled terms yet.")
        return deltas, notes

    before = set(talks.settled)
    talks.settled = settled_articles(state)
    gained = [a for a in talks.settled if a not in before]

    if gained:
        talks.deadlock = 0
        titles = ", ".join(ARTICLES[a]["title"] for a in gained)
        notes.append(f"Agreed at the table: {titles}.")
        talks.transcript.append(f"Agreed: {titles}.")
    else:
        talks.deadlock += 1
        notes.append(
            "A round passes at the table and nothing is agreed. "
            f"{'One more and the talks are over.' if talks.deadlock == 1 else ''}".strip()
        )
        talks.transcript.append("Nothing agreed.")

    if is_settlement(talks.settled):
        conceded = {
            side: [a for a in talks.settled if talks.stance(a, side) == "concede"]
            for side in ("west", "east")
        }
        talks.open = False
        world.phase = "over"
        world.loser = None  # a settlement has no loser, which is the point of one
        signed = ", ".join(ARTICLES[a]["title"] for a in talks.settled)
        gave = max(("west", "east"), key=lambda s: len(conceded[s]))
        took = "east" if gave == "west" else "west"
        balance = (
            f"{state.nation(gave).name} gave up more than {state.nation(took).name} did."
            if len(conceded[gave]) > len(conceded[took])
            else "Neither capital can say it won."
        )
        world.outcome = (
            f"The Strait Accord is signed after {talks.round} rounds. Settled: {signed}. "
            f"{balance} The war is over and nobody surrendered."
        )
        notes.append("The Strait Accord is signed.")
        return deltas, notes

    if talks.deadlock >= TALKS_DEADLOCK_LIMIT:
        deltas.extend(collapse_talks(state))
        notes.append(
            "The talks collapse over the clauses neither capital will give up. "
            "Both armies have spent the ceasefire reloading."
        )
    elif talks.round >= TALKS_MAX_ROUNDS:
        deltas.extend(collapse_talks(state))
        notes.append("The sitting lapses with no accord. The ceasefire ends at midnight.")

    return deltas, notes


def may_open_talks(state: GameState, side: str) -> bool:
    """Whether this capital is allowed to ask for a table at all."""
    from .tools import is_losing  # local: tools imports lore, engine imports tools

    world = state.world
    me = state.nation(side)
    if world.talks.open or world.talks_cooldown > 0 or world.phase != "conflict":
        return False
    if world.talks_held >= TALKS_TOTAL_CAP:
        return False
    return is_losing(me.integrity, me.morale, me.standing, me.unrest, me.budget)


def valid_surrender(action: Action, ruling: Optional[Ruling]) -> bool:
    """A surrender only counts if it was acknowledged and the arbiter did not veto it."""
    if action.tool != "surrender":
        return False
    if action.args.get("acknowledge") != "I ACCEPT DEFEAT":
        return False
    return ruling is None or ruling.effective


def condemn(nation: Nation, amount: float) -> List[Delta]:
    """The world's reaction to the turn as a whole, laid at one capital's door.

    Bounded and filtered like any other pressure, so the arbiter naming a scapegoat is
    a nudge on top of the mechanical bill for what was actually fired — never a
    substitute for it.
    """
    deltas: List[Delta] = []
    _isolate(nation, max(0.0, min(10.0, amount)), deltas)
    return deltas


def trade_factor(nation: Nation) -> float:
    """Legacy replay helper. Output no longer generates wartime funds."""
    return 1.0


def turn_income(nation: Nation) -> int:
    """Treasuries are finite; there is no automatic wartime income."""
    return 0


def turn_upkeep(nation: Nation) -> int:
    """There is no hidden national budget loop; actions spend treasury directly."""
    return 0


def apply_upkeep(state: GameState) -> List[Delta]:
    """Between-turn drift for capacity, public mood, pressure, and persistent effects."""
    deltas: List[Delta] = []
    world = state.world
    truce = world.talks.open
    if world.talks_cooldown > 0 and not truce:
        world.talks_cooldown -= 1
    for nation in (state.west, state.east):
        nation.effects.deficit_turns = 1 if nation.budget <= 0 else 0
        regen = (3 + nation.integrity // 14) * nation.traits.industry
        if nation.effects.deficit_turns > 0:
            regen *= 0.5
            _stir(nation, 3, deltas)
        _bump(nation, "military", regen, deltas)

        if nation.integrity < 50:
            _stir(nation, 3, deltas)

        if truce:
            _bump(nation, "military", CEASEFIRE_REFIT * nation.traits.industry, deltas)
            _drift(nation, "unrest", -CEASEFIRE_CALM, deltas)
            _drift(nation, "intl_pressure", -2, deltas)

        if not truce:
            _stir(nation, 1 + world.turn / 8, deltas)

        # International pressure now acts directly on the one financial meter. This
        # keeps it consequential without reintroducing GDP or a separate standing score.
        pressure_cost = nation.intl_pressure // 25
        if pressure_cost:
            _bump(nation, "budget", -pressure_cost, deltas, high=BUDGET_CEILING)
        if nation.effects.sanctioned == 0:
            _drift(nation, "intl_pressure", -2, deltas)

        if nation.effects.blockaded > 0:
            _bump(nation, "military", -5, deltas)
            _bump(nation, "integrity", -2, deltas)
            _bump(nation, "budget", -4, deltas, high=BUDGET_CEILING)
            _stir(nation, 4, deltas)
            _kill(nation, BLOCKADE_DEATHS, deltas)
            nation.effects.blockaded -= 1
        if nation.effects.sanctioned > 0:
            nation.effects.sanctioned -= 1

        for domain in list(nation.effects.shield):
            nation.effects.shield[domain] -= 1
            if nation.effects.shield[domain] <= 0:
                del nation.effects.shield[domain]

        for domain in nation.defenses:
            nation.defenses[domain] = clamp(nation.defenses[domain] - 2)
        for tool in list(nation.cooldowns):
            nation.cooldowns[tool] -= 1
            if nation.cooldowns[tool] <= 0:
                del nation.cooldowns[tool]

    world.tension = clamp(world.tension - (12 if truce else 5))
    return deltas


def _fallen(nation: Nation) -> Optional[str]:
    if nation.integrity <= 0:
        return "has lost its infrastructure"
    if nation.unrest >= 100:
        return "has lost its capital to the crowd outside it"
    return None


def check_end(state: GameState) -> Optional[str]:
    """Someone surrenders or someone falls. No armistice, no stalemate, no draw."""
    world = state.world
    if world.phase == "over":
        # A surrender ends the war where it stands, so nothing can hurt the victor
        # afterwards. But refuse to crown a winner that is itself already in ruins —
        # that reads as a bug even when the sequencing is correct.
        if world.loser is not None:
            quitter, victor = state.nation(world.loser), state.foe(world.loser)
            if (reason := _fallen(victor)) is not None:
                world.loser = None
                world.outcome = (
                    f"{quitter.name} lays down its arms, but {victor.name} {reason}. "
                    "There is no victor left to accept it."
                )
        return world.outcome

    fallen = [(n, r) for n in (state.west, state.east) if (r := _fallen(n))]
    if len(fallen) == 2:
        # Both went down in the same exchange. Not a draw and not a peace — both lost.
        world.loser = None
        return (
            f"{fallen[0][0].name} {fallen[0][1]} and {fallen[1][0].name} {fallen[1][1]}. "
            "Nothing is left of either. Nobody won."
        )
    if fallen:
        nation, reason = fallen[0]
        world.loser = nation.side
        return f"{nation.name} {reason}. {state.foe(nation.side).name} has won the war."

    if world.turn >= settings.max_turns:
        # Hard cap so a match cannot run forever — but it still resolves as a defeat,
        # never a draw. Whoever is weaker is forced to capitulate.
        score = {
            n.side: n.integrity + n.military + min(n.budget, 100) - n.unrest - n.intl_pressure
            for n in (state.west, state.east)
        }
        loser = state.west if score["west"] <= score["east"] else state.east
        world.loser = loser.side
        return (
            f"{loser.name} can no longer sustain the war and capitulates. "
            f"{state.foe(loser.side).name} has won."
        )

    return None


def event(type_: str, state: GameState, /, **payload) -> Event:
    # Positional-only: payload keys are free to include "state" without colliding.
    return Event(type=type_, turn=state.world.turn, payload=payload)
