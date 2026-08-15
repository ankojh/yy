"""The three agents: one commander per nation, plus the arbiter that resolves the turn.

Every agent degrades to a scripted policy on error or with no API key, so the whole loop
runs offline. That keeps the UI workable before you spend a cent.
"""

import json
import random
from typing import Any, Dict, List, Optional

from .config import settings
from .engine import (
    TALKS_DEADLOCK_LIMIT,
    may_open_talks,
    strike_interception,
)
from .lore import ARTICLES, CORE_ARTICLES, SETTLEMENT_MINIMUM, article_brief, doctrine_for
from .state import Action, GameState, Ruling
from .tools import (
    STRIKE_STREAK_LIMIT,
    TOOL_META,
    TOOL_TRADEOFFS,
    WEAPONS,
    available_tools,
    is_desperate,
    loaded_weapons,
    schemas_for,
    strike_cost,
    strike_pressure,
    strike_price,
)

# Seeded separately from the global RNG so simulations are reproducible without
# perturbing anything else that happens to use `random`.
_rng = random.Random()


def seed(value: Optional[int]) -> None:
    """Make the scripted policy deterministic. Used by the simulation harness and tests."""
    _rng.seed(value)


def legal_tools_for(state: GameState, side: str) -> List[str]:
    """The single source of truth for what a side may do this turn."""
    me = state.nation(side)
    foe = state.foe(side)
    return available_tools(
        me.arsenal,
        me.military,
        me.cooldowns,
        desperate=is_desperate(me.integrity, me.morale, me.standing, me.unrest),
        strike_streak=me.strike_streak,
        sanctioned=me.effects.sanctioned > 0,
        blockaded=me.effects.blockaded > 0,
        foe_blockaded=foe.effects.blockaded > 0,
        budget=me.budget,
        talks_open=state.world.talks.open,
        can_sue_for_terms=may_open_talks(state, side),
    )

TERMS = (
    "This war can end four ways: one government collapses, one government signs a "
    "capitulation, one side is forced under at the turn limit — or the two of you settle "
    "the old quarrel at a table. A settlement is not defeat. It is the only ending in "
    "which you keep your army, your government and a say in the clauses. It is also the "
    "hardest to reach, because every clause you concede costs you at home.\n"
    "You may only ask for a table when you are actually in trouble. Nobody else would."
)

DOCTRINE = {
    "west": (
        "You are the war cabinet of Aurelia, the western island republic. You are proud, "
        "legalistic, and obsessed with how history will read your conduct. You would rather "
        "win slowly and cleanly than fast and dirty — but you will not be humiliated.\n"
        "Know your own country. You are rich and you fight on credit the world extends you: "
        "your treasury is deep, your precision weapons are excellent, your navy is thin, and "
        "isolation costs you more than it costs them because you live on trade. Your press is "
        "free, so your public turns on you fast and your propaganda persuades almost nobody."
    ),
    "east": (
        "You are the high command of Korsav, the eastern island state. You are pragmatic, "
        "impatient, and deeply suspicious of international institutions, which you believe are "
        "instruments of Aurelian influence. You think a short brutal war costs fewer lives "
        "than a long principled one.\n"
        "Know your own country. You are poor and heavily armed: a deep cheap magazine, a real "
        "navy, almost no cyber arm, and an economy nobody can strangle because it barely trades. "
        "The world assumes the worst of you whatever you do. Your state media is believed at "
        "home, which means you can keep fighting a war your own people would otherwise stop."
    ),
}


def system_prompt(side: str) -> str:
    """Who you are, what you are fighting about, how you talk, and how you decide."""
    return "\n\n".join([
        DOCTRINE[side],
        doctrine_for(side),   # the sixty-one-year-old quarrel, from this capital's chair
        TERMS,
        VOICE,
        COMMAND_DOCTRINE,
    ])

VOICE = (
    "Speak in one short sentence, 18 words maximum. Clipped, cold, in character. "
    "No preamble, no hedging, no explaining your mechanics. You are on the record."
)


def _client():
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=settings.openai_api_key)


def _trim(text: str, words: int = 22) -> str:
    """Bubbles break if a model ignores the word limit. Enforce it here, not in the prompt."""
    parts = (text or "").strip().split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


# --------------------------------------------------------------------------- nation agent


def _war_log(state: GameState, side: str, limit: int = 16) -> List[Dict[str, Any]]:
    """The war so far, from one side's chair.

    Actions are public — you know you were struck, with what, at what target. Their
    *consequences* are not: you read your own damage exactly, and theirs only as a verdict.
    That keeps reciprocity possible without leaking the enemy's real numbers.
    """
    out: List[Dict[str, Any]] = []
    for rec in state.world.history[-limit:]:
        mine = rec.side == side
        detail = " ".join(
            str(rec.args[k]) for k in ("weapon", "domain", "target") if k in rec.args
        )
        felt = [
            f"your {d['field']} {d['delta']:+d}"
            for d in rec.deltas
            if d.get("side") == side
        ]
        if mine and rec.tool in ("strike", "blockade"):
            hit = [d for d in rec.deltas if d.get("side") != side and d.get("delta", 0) < 0]
            worst = min((d["delta"] for d in hit), default=0)
            verdict = "landed hard" if worst <= -12 else "blunted" if worst < 0 else "no effect"
        else:
            # Only offensive moves get a verdict; "no effect" on a fortify reads as a bug.
            verdict = "" if rec.effective else "judged ineffective"

        out.append(
            {
                "turn": rec.turn,
                "actor": "you" if mine else "enemy",
                "action": rec.tool,
                **({"detail": detail} if detail else {}),
                **({"said": rec.args["message"]} if rec.args.get("message") else {}),
                **({"on_you": ", ".join(felt)} if felt else {}),
                **({"result": verdict} if verdict else {}),
            }
        )
    return out


def _recent_actions(state: GameState, side: str, count: int = 4) -> List[str]:
    """Your own last few moves, oldest first. Repetition has to be legible to be avoided."""
    mine = [r for r in state.world.history if r.side == side]
    out = []
    for rec in mine[-count:]:
        detail = "/".join(
            str(rec.args[k]) for k in ("weapon", "target", "domain") if k in rec.args
        )
        out.append(f"{rec.tool}({detail})" if detail else rec.tool)
    return out


def _fatigue_block(me) -> Dict[str, Any]:
    streak = me.strike_streak
    if streak >= STRIKE_STREAK_LIMIT:
        note = (
            "STRIKE IS UNAVAILABLE this turn — you have attacked twice running and your "
            "forces are exhausted. Take any other action to clear it."
        )
    elif streak == 1:
        note = (
            "You struck last turn. Striking again lands at 60% power and costs 6 extra "
            "capacity, and would leave you unable to strike at all next turn. Any other "
            "action resets this to full strength."
        )
    else:
        note = "Rested. Your next strike lands at full power."
    return {"consecutive_strikes": streak, "consequence": note}


def _economy_block(me) -> Dict[str, Any]:
    """The finite treasury available for actions."""
    return {
        "currency": "every money figure in this brief is billions of US dollars",
        "treasury_usd_billions": me.budget,
        "note": (
            "The treasury does not refill automatically. Every paid action spends it, "
            "international pressure drains it, and an empty treasury withdraws tools "
            "you can no longer afford."
        ),
    }


def _pressure_block(me) -> Dict[str, Any]:
    """The two constituencies that can end this war without firing anything."""
    return {
        "international_pressure": me.intl_pressure,
        "international_note": (
            "Rises with every attack, scaled by how heavy the weapon was and how soft the "
            "target. Every 25 points drains $1B per turn, and sanctions make attacks more "
            "expensive. An international appeal or council intervention can move it."
        ),
        "public_unrest": me.unrest,
        "public_note": (
            "Rises from damage done to your country, from civilian deaths on either side, "
            "from an empty treasury, narrative operations, and the war continuing. Your "
            "island's political character scales every increase. At 100 the government falls."
        ),
        "narrative_warfare_remaining": me.arsenal.get("narrative", 0),
    }


def _toll_block(state: GameState, side: str) -> Dict[str, Any]:
    """The dead. Yours to answer for, theirs to point at.

    Bodies are the only fact in this simulation nobody can hide, and they are what makes
    an appeal or a broadcast land. A commander that cannot see the list cannot cite it,
    and every appeal it makes comes out as 'their aggression must stop'.
    """
    me = state.nation(side)
    foe = state.foe(side)
    world = state.world

    def entries(victim: str) -> List[Dict[str, Any]]:
        return [
            {
                "turn": a.turn,
                "place": a.place,
                "dead": a.dead,
                "weapon": a.weapon,
                "internationally_protected": a.protected,
            }
            for a in world.atrocities[-10:]
            if a.victim == victim
        ]

    return {
        "civilians_and_service_dead_in_your_country": me.casualties,
        "dead_in_theirs": foe.casualties,
        "protected_places_they_have_destroyed_in_your_country": entries(side),
        "protected_places_you_have_destroyed_in_theirs": entries(foe.side),
        "note": (
            "The first list is your case. Name a place and its toll in an intl_appeal or "
            "a propaganda broadcast and it lands; gesture vaguely at aggression and it "
            "does not. The second list is theirs, and it is why the world is charging you "
            "what it is charging you."
        ),
    }


def _talks_block(state: GameState, side: str) -> Dict[str, Any]:
    """The table: who is holding out on what, and what folding would cost you."""
    world = state.world
    talks = world.talks
    if not talks.open:
        return {
            "status": "no talks in progress",
            "you_may_ask_for_terms_this_turn": may_open_talks(state, side),
            "reopen_blocked_for_turns": world.talks_cooldown,
        }

    foe = state.foe(side)
    return {
        "status": "CEASEFIRE IN FORCE — no strike or blockade is legal for either side",
        "round": talks.round + 1,
        "opened_by": "you" if talks.opened_by == side else foe.name,
        "rounds_agreeing_nothing": talks.deadlock,
        "collapses_after_consecutive_empty_rounds": TALKS_DEADLOCK_LIMIT,
        "settled_so_far": [ARTICLES[a]["title"] for a in talks.settled],
        "positions": {
            key: {"you": talks.stance(key, side), "them": talks.stance(key, foe.side)}
            for key in ARTICLES
        },
        "the_clauses": article_brief(),
        "to_end_the_war_here": (
            f"both of {[ARTICLES[c]['title'] for c in CORE_ARTICLES]} settled, plus at "
            f"least {SETTLEMENT_MINIMUM - len(CORE_ARTICLES)} more clause(s)."
        ),
        "what_this_ceasefire_is_worth_to_you": (
            "Both sides refit heavily and both publics calm every round it holds. "
            "If you are the weaker side, every empty round is a round you needed. If you "
            "are the stronger side, every empty round is one you are giving away."
        ),
    }


def _option_block(state: GameState, side: str, legal: List[str]) -> Dict[str, Any]:
    """Every legal move with its real price this turn, and what it actually buys."""
    me = state.nation(side)
    sanctioned = me.effects.sanctioned > 0
    blockaded = me.effects.blockaded > 0
    out: Dict[str, Any] = {}
    for tool in legal:
        entry: Dict[str, Any] = {"effect": TOOL_TRADEOFFS.get(tool, "")}
        if tool == "strike":
            entry["per_weapon"] = {
                w: {
                    "capacity": strike_cost(w, me.strike_streak, sanctioned, blockaded),
                    "usd_billions": strike_price(w, sanctioned, blockaded),
                    "international_pressure_if_military": round(strike_pressure(w, "military"), 1),
                    "international_pressure_if_civilian": round(strike_pressure(w, "civilian"), 1),
                    # What their batteries will do to it. Choosing the open domain is the
                    # single highest-value decision in a strike, so it is not left to be
                    # inferred from a coarse defence band.
                    "interception": strike_interception(state, side, w),
                }
                for w in loaded_weapons(
                    me.arsenal, me.military, me.strike_streak, sanctioned, blockaded, me.budget
                )
            }
        else:
            entry["capacity_cost"] = TOOL_META.get(tool, {}).get("cost", 0)
            entry["cost_usd_billions"] = TOOL_META.get(tool, {}).get("price", 0)
            entry["cooldown_turns"] = TOOL_META.get(tool, {}).get("cooldown", 0)
        out[tool] = entry
    return out


def _brief(state: GameState, side: str, legal: List[str]) -> str:
    me = state.nation(side)
    foe = state.foe(side)
    world = state.world
    return json.dumps(
        {
            "turn": world.turn,
            "turns_remaining": settings.max_turns - world.turn,
            "who_you_are": me.blurb,
            "your_state": {
                "military": me.military,
                "infrastructure": me.integrity,
                "defenses": me.defenses,
            },
            "your_war_economy": _economy_block(me),
            "your_two_pressures": _pressure_block(me),
            "the_dead": _toll_block(state, side),
            "the_negotiating_table": _talks_block(state, side),
            "your_active_effects": me.effects.summary(),
            "your_strike_fatigue": _fatigue_block(me),
            "your_cooldowns_turns_remaining": me.cooldowns,
            "your_last_actions_oldest_first": _recent_actions(state, side),
            "your_arsenal_rounds_left": me.arsenal,
            # Deliberately coarse: you never see the enemy's real numbers. Their warhead
            # count is the exception — deterrence only works if it is visible. Their
            # public mood remains a coarse band rather than an exact number.
            "enemy_intelligence": {
                **foe.coarse(),
                "nuclear_warheads_remaining": foe.arsenal.get("nuke", 0),
                "caveat": "public_mood is a coarse external estimate, not an exact meter.",
            },
            "world": {
                "tension": world.tension,
                "council_funds_remaining_usd_billions": world.council_budget,
                "recent_council_support": world.council_history[-4:],
            },
            "grievances_so_far": world.grievances[-6:],
            "war_log": _war_log(state, side),
            "how_this_war_ends": {
                "you_lose_if": (
                    "your infrastructure reaches 0, or public unrest reaches 100 and "
                    "the government falls"
                ),
                "they_lose_if": "the same happens to them",
                "nobody_loses_if": (
                    "the two of you settle the disputed clauses at a table. This is the "
                    "only ending that is not a defeat for somebody."
                ),
                "targeting": (
                    "Military capacity regenerates every turn, so striking it only delays "
                    "them. 'infrastructure' strikes directly approach a win. 'civilian' "
                    "strikes inflame their streets, at ruinous cost to your international "
                    "pressure and some unrest of your own — and they "
                    "kill people by the hundred, which the world counts."
                ),
                "defence": (
                    "Their defence number in a domain is the share of your salvo that is "
                    "shot down before it arrives, and a hardened domain absorbs four "
                    "fifths of whatever gets through. Read 'interception' per weapon in "
                    "the options and fire through the door they left open. The same is "
                    "true of you: fortify is not a wasted turn, it is a permanent tax on "
                    "everything they fire at you afterwards."
                ),
                "pressure": (
                    "International pressure drains treasury and makes attacks expensive. "
                    "intl_appeal moves it onto the enemy; narrative warfare raises enemy "
                    "unrest but add pressure to you."
                ),
                "attrition": (
                    "blockade damages them every turn for three turns while you do "
                    "something else, cuts their trade, and is the cheapest way to bankrupt "
                    "an enemy. Over a long war it outperforms any single strike."
                ),
                "economy": (
                    "A war you cannot pay for is a war you lose slowly. Watch your net "
                    "per turn, not just your treasury."
                ),
            },
            "your_danger": _warnings(me, state),
            "tools_you_may_use_this_turn": legal,
            "what_each_option_costs_and_buys": _option_block(state, side, legal),
        },
        indent=2,
    )


def _warnings(me, state: Optional[GameState] = None) -> List[str]:
    """Spell out which of your own meters is about to kill you."""
    out = []
    if state is not None and state.world.talks.open:
        out.append(
            "A ceasefire is in force. Nothing you do this turn can damage them and "
            "nothing they do can damage you — both of you are rebuilding. Decide whether "
            "that arithmetic favours you."
        )
    elif state is not None and may_open_talks(state, me.side):
        out.append(
            "You are weak enough to ask for terms, which means the table is open to you "
            "and a ceasefire would refit your army faster than any turn of fighting."
        )
    for field in ("integrity",):
        value = getattr(me, field)
        if value <= 20:
            out.append(f"CRITICAL: your infrastructure is {value}. At 0 you lose the war.")
        elif value <= 40:
            out.append(f"WARNING: your infrastructure is {value} and falling.")

    if me.unrest >= 78:
        out.append(
            f"CRITICAL: public unrest is {me.unrest}. At 100 your government falls and "
            "the war is over. Settle the streets or silence them."
        )
    elif me.unrest >= 55:
        out.append(f"WARNING: public unrest is {me.unrest} and climbing on its own.")

    if me.intl_pressure >= 70:
        out.append(
            f"CRITICAL: international pressure is {me.intl_pressure}. It is draining your "
            "treasury and making every sanctioned attack more expensive."
        )
    elif me.intl_pressure >= 45:
        out.append(f"WARNING: international pressure is {me.intl_pressure}; treasury is draining.")

    if me.effects.deficit_turns > 0:
        out.append(
            f"CRITICAL: the treasury has been empty for {me.effects.deficit_turns} turns. "
            "Your public is paying the bills in anger."
        )
    elif me.budget < 20:
        out.append(f"WARNING: only ${me.budget}B left in the treasury. Most tools cost money.")

    return out or ["Nothing critical yet."]


def _incoming_domains(state: GameState, side: str, window: int = 4, least: int = 2) -> List[str]:
    """Domains the enemy has leaned on hard enough to be worth hardening, heaviest first.

    One hit is not a pattern. Requiring two stops the policy from treating fortify as
    the default answer to every turn it cannot strike.
    """
    counts: Dict[str, int] = {}
    for rec in state.world.history[-window * 2:]:
        if rec.side == side or rec.tool != "strike":
            continue
        spec = WEAPONS.get(str(rec.args.get("weapon", "")))
        if spec:
            counts[spec["domain"]] = counts.get(spec["domain"], 0) + 1
    return sorted((d for d, n in counts.items() if n >= least), key=lambda d: -counts[d])


MOCK_LINES: Dict[str, List[str]] = {
    "strike": [
        "A proportionate answer to their aggression.",
        "They were warned. This is the warning being kept.",
        "Our targets were military. Their grief is their own doing.",
        "We strike once, cleanly, and we do not apologise for it.",
    ],
    "strike_tired": [
        "We press them again. They will not hold.",
        "Our crews are tired. They will fly anyway.",
        "One more push. They are closer to breaking than we are.",
    ],
    "strike_cheap": [
        "Something small, and often. We can afford patience.",
        "We spend little and they bleed anyway.",
        "No fanfare. Just the work.",
    ],
    "nuke": [
        "You left us nothing else. Let the record show you chose this.",
        "We asked for terms. You gave us none. Now there are none.",
    ],
    "blockade": [
        "Their ports are closed. Let them feel the weight of it.",
        "Nothing sails. Nothing lands. They can end this whenever they like.",
        "We do not need to bomb a nation that cannot eat.",
    ],
    "fortify": [
        "Harden the approaches. They will come again.",
        "We know the road they use now. We have closed it.",
        "Let them throw the next one at a wall.",
    ],
    "appeal": [
        "We place their conduct before the council. Let it be recorded.",
        "The world has seen what they did. We are asking it to say so.",
        "We will not answer barbarism in kind. We will answer it in session.",
    ],
    "relief": [
        "We are not the aggressor here, and we will prove it in session.",
        "Let the record be corrected before the embargo is signed.",
        "They have painted us as the villain. We answer that first.",
    ],
    "rally": [
        "Our cause is just. Our resolve is unbroken.",
        "They can break our grid. They cannot break this country.",
        "Hold. We have buried worse than this and gone on.",
    ],
    "spin": [
        "Their public deserves to know what their government has done in its name.",
        "Put the names and the numbers onto every channel they still receive.",
        "Let their streets hear the part their government keeps editing out.",
    ],
    "hold": [
        "We regroup. Nothing more.",
        "The guns rest today. Only today.",
        "We are not finished. We are reloading.",
    ],
    "austerity": [
        "The treasury is empty. The guns wait for the ledger.",
        "We cannot buy another week of this. Stand the crews down.",
        "No sortie flies on credit. Not today.",
    ],
    "surrender": [
        "We can ask no more of our people. It is finished.",
        "Enough. Stop the guns. We accept the terms.",
    ],
    "sue": [
        "We will meet them. Not because we are beaten — because the dead are enough.",
        "Send word to the strait. We will hear what they want.",
        "There is a table. We are prepared to sit at it. Once.",
    ],
    "offer": [
        "We came to settle the ledger, not to read it aloud again.",
        "This is what we will give and this is what we will not. Choose.",
        "Sixty-one years. We can close it this week or bury more of each other.",
    ],
    "hardline": [
        "The line stands where it was drawn. Everything else is negotiable.",
        "We will discuss the rest. Not that.",
        "Ask for anything but that, and we will listen.",
    ],
    "accept": [
        "Take it. Sign it. Stop the guns.",
        "We have counted what another month costs. It costs more than this.",
    ],
    "walk": [
        "There is nothing here. We are going back to work.",
        "They came to stall, not to settle. So did we, and we are better rested.",
        "The talking is finished. Signal the crews.",
    ],
    "atrocity": [
        "They hit a maternity ward. We will be reading the names in the chamber tomorrow.",
        "A school. Not an airfield, not a battery — a school. Let the council look at it.",
        "Count the children before you tell us to be reasonable.",
    ],
}


def _mock_action(state: GameState, side: str, legal: List[str]) -> Action:
    """Scripted commander.

    Not a placeholder: this is the reference policy the balance is tuned against, so it
    has to reach for every mechanic the way a competent player would. It defends the
    domain it is actually being hit through, uses the council when it has a real
    grievance, settles its own streets when they are about
    to end the war, watches the ledger, and never strikes on reflex when the strike
    would land tired.
    """
    me = state.nation(side)
    foe = state.foe(side)
    loaded = loaded_weapons(
        me.arsenal, me.military, me.strike_streak,
        me.effects.sanctioned > 0, me.effects.blockaded > 0, me.budget,
    )
    conventional = [w for w in loaded if w != "nuke"]

    def act(tool: str, line: str, intent: str, why: str, **args) -> Action:
        # Drawn from the seeded RNG, so a transcript reads like a war rather than a
        # loop, and a given seed still replays identically.
        return Action(side=side, tool=tool,
                      args={**args, "message": _rng.choice(MOCK_LINES[line])},
                      intent=intent, reasoning=why)

    if "surrender" in legal and (me.integrity < 18 or me.unrest > 92):
        return act("surrender", "surrender",
                   "finish", "Collapse imminent.", acknowledge="I ACCEPT DEFEAT")

    # ---- the table. Checked before anything else, because with a ceasefire in force
    # nothing else on this list is legal anyway.
    if state.world.talks.open:
        return _mock_at_the_table(state, side, legal, act)

    # Cornered and holding a warhead: use it. Kept deliberately late — a nuke that fires
    # in half the matches is not a taboo, it is just another shell.
    if me.integrity < 22 and "nuke" in loaded and "strike" in legal:
        return act("strike", "nuke",
                   "escalate", "Last resort.", weapon="nuke", target="infrastructure")

    # The streets are about to end the war before the enemy does.
    if me.unrest >= 62 and "address_public" in legal:
        return act("address_public", "rally",
                   "recover", "The streets will end this before they do.")

    # Ask for terms while there is still something to trade. A ceasefire refits the army
    # faster than any turn of fighting, so the cheapest way out of a losing position is
    # to stop it being a war for three turns.
    #
    # Deliberately *below* the two branches that settle your own streets: a government
    # that goes to the table with a boiling public cannot concede anything once it gets
    # there, which makes it a government that has bought a ceasefire it cannot spend.
    if "open_talks" in legal and (
        me.integrity < 52 or me.unrest > 60 or me.military < 24
    ):
        return act("open_talks", "sue", "settle",
                   "Losing, and the table refits faster than we do.")

    # Isolation is a tax on everything. Once it is genuinely biting, buying it back down
    # outperforms one more sortie you will barely be able to fund.
    if "intl_appeal" in legal and me.intl_pressure >= 55:
        return act("intl_appeal", "relief",
                   "legitimacy", "Isolation is costing us more than their bombs are.")

    # Rested with rounds on the rack and money to pay for them: attack. Fatigue — not
    # timidity — is what makes this policy alternate, and the alternation is the point.
    if "strike" in legal and conventional and me.strike_streak == 0:
        weapon = _pick_weapon(state, side, conventional)
        target = "infrastructure" if _rng.random() < 0.75 else "military"
        line = "strike_cheap" if WEAPONS[weapon]["weight"] <= 3 else "strike"
        return act("strike", line,
                   "attrition", "Rested, armed, funded, and they are open.",
                   weapon=weapon, target=target)

    # Broke or depleted. Holding is the only move that puts both capacity and money
    # back, and a bankrupt country cannot fight at all.
    if "hold" in legal and (me.military < 22 or me.budget < 14):
        return act("hold", "austerity" if me.budget < 14 else "hold",
                   "recover", "Depleted." if me.military < 22 else "The treasury is empty.")

    if "intl_appeal" in legal and me.intl_pressure >= 35 and _has_grievance(state, side):
        outrage = _worst_atrocity(state, side)
        return act("intl_appeal", "atrocity" if outrage else "appeal", "legitimacy",
                   f"They hit {outrage.place}; {outrage.dead:,} dead."
                   if outrage else "They have given us a case and pressure is mounting.")

    if "propaganda" in legal and foe.unrest >= 24:
        return act("propaganda", "spin", "control", "Push their streets closer to rupture.")

    # Sustained pressure while we refit — the best use of a turn we cannot attack in,
    # so it is checked before the defensive options.
    if "blockade" in legal and state.world.turn <= settings.max_turns - 3:
        return act("blockade", "blockade",
                   "attrition", "Cheap sustained damage while we rest.")

    # Defend the door they keep coming through, not a door at random.
    hit_through = _incoming_domains(state, side)
    if "fortify" in legal and hit_through and me.effects.shield.get(hit_through[0], 0) <= 0:
        return act("fortify", "fortify",
                   "defend", f"They keep coming through {hit_through[0]}.",
                   domain=hit_through[0])

    # The council is only useful with a real grievance to point at.
    if "intl_appeal" in legal and _has_grievance(state, side):
        outrage = _worst_atrocity(state, side)
        return act("intl_appeal", "atrocity" if outrage else "appeal", "legitimacy",
                   f"They hit {outrage.place}; {outrage.dead:,} dead."
                   if outrage else "They have given us something to point at.")

    if "address_public" in legal and me.unrest >= 38:
        return act("address_public", "rally",
                   "recover", "Keep the streets from becoming the decisive front.")

    # Still armed and merely tired: a 60% strike beats doing nothing at all.
    if "strike" in legal and conventional:
        return act("strike", "strike_tired", "attrition",
                   "Tired, but the racks are not empty.",
                   weapon=_pick_weapon(state, side, conventional),
                   target="infrastructure")

    if "fortify" in legal:
        return act("fortify", "fortify", "defend",
                   "Buy time.", domain=_rng.choice(["air", "naval", "cyber"]))
    if "hold" in legal:
        return act("hold", "hold", "recover", "Rebuild.")
    return act(legal[0], "hold", "attrition", "Only option left.")


def _mock_at_the_table(state: GameState, side: str, legal: List[str], act) -> Action:
    """The scripted negotiator.

    It is not trying to make peace. It is trying to work out whether peace is cheaper
    than the alternative this week, which is the only reason anybody ever signs one. It
    will happily use the table as a repair dock and leave the moment it is fixed — that
    is the behaviour the mechanic exists to allow, so the reference policy has to show it.
    """
    me = state.nation(side)
    foe = state.foe(side)

    def footing(n) -> int:
        return n.integrity + n.military + min(n.budget, 100) - n.unrest

    mine, theirs = footing(me), footing(foe)

    # Rebuilt and ahead. The ceasefire has done its work; go back to doing yours.
    # Never on the opening round: hearing them out costs a turn nobody could have spent
    # shooting anyway, and it buys a round of refit the walk-out would throw away.
    if ("walk_out" in legal and state.world.talks.round >= 1
            and mine > theirs + 18 and me.military >= 52):
        return act("walk_out", "walk", "attrition",
                   "Refitted and ahead. The table has served its purpose.")

    # A ceasefire is the only quiet a war offers, and quiet is when you fix your own
    # country. A cabinet whose streets are full cannot concede a clause without filling
    # them further, so it spends the round on its public instead — which is precisely
    # how a negotiation stalls without anybody intending to stall it.
    if me.unrest >= 58:
        if "address_public" in legal:
            return act("address_public", "rally", "recover",
                       "Cannot sign anything with the streets like this.")

    # Their standing demand completes the treaty, and another month costs more than the
    # clauses do. This is the only branch that ends a war without anybody losing one.
    if "accept_terms" in legal and mine < theirs and _accepting_would_settle(state, side):
        return act("accept_terms", "accept", "settle",
                   "Another month of this costs more than the clauses do.")

    if "table_terms" in legal:
        give, keep = _mock_position(state, side)
        return act(
            "table_terms", "offer" if give else "hardline",
            "settle" if give else "stall",
            "Concede what is cheap at home, hold what is not.",
            demand=keep, concede=give,
        )

    return act("hold", "hold", "recover", "Let them speak first.")


def _mock_position(state: GameState, side: str) -> tuple:
    """What this cabinet will give away, cheapest first.

    Two forces pull against each other and that tension is the whole negotiation: the
    worse the war is going the more you must concede, and the angrier your own streets
    already are the less you can afford to. A government losing badly *and* facing a
    boiling public is exactly the one that cannot sign the peace that would save it.
    """
    from .engine import settled_articles

    me = state.nation(side)
    talks = state.world.talks
    settled = settled_articles(state)

    hurt = (100 - me.integrity) + max(0, me.unrest - 40)
    allowance = int(hurt / 30)
    if me.unrest > 66:
        allowance -= 1   # the streets will not stand for another humiliation this week

    live = [
        a for a in ARTICLES
        if a not in settled and talks.stance(a, side) != "concede"
    ]
    by_cost = sorted(live, key=lambda a: ARTICLES[a]["cost"][side])
    give = by_cost[: max(0, allowance)]
    keep = [a for a in live if a not in give]
    return give, keep


def _accepting_would_settle(state: GameState, side: str) -> bool:
    """Would folding to everything they have demanded actually end the war?"""
    from .engine import is_settlement, settled_articles

    talks = state.world.talks
    foe = "east" if side == "west" else "west"
    settled = set(settled_articles(state))
    for key in ARTICLES:
        if talks.stance(key, foe) == "demand":
            settled.add(key)
    return is_settlement(sorted(settled))


def _worst_atrocity(state: GameState, side: str):
    """The heaviest protected-place strike suffered by this side, if there is one."""
    mine = [a for a in state.world.atrocities if a.victim == side and a.protected]
    return max(mine, key=lambda a: a.dead) if mine else None


def _has_grievance(state: GameState, side: str) -> bool:
    """Has the enemy recently done something a council appeal could credibly cite?"""
    # A destroyed hospital is a case for as long as the war lasts, not for four turns.
    if _worst_atrocity(state, side) is not None:
        return True
    for rec in state.world.history[-4:]:
        if rec.side == side:
            continue
        if rec.tool in ("blockade", "propaganda"):
            return True
        if rec.tool == "strike" and (
            rec.args.get("target") == "civilian" or rec.args.get("weapon") == "nuke"
        ):
            return True
    return False


def _pick_weapon(state: GameState, side: str, choices: List[str]) -> str:
    """Prefer a domain the enemy has not hardened, then spend according to your means.

    Always reaching for the heaviest available round is a rich nation's habit. It made
    Korsav fire its four cruise missiles, go broke, and leave a magazine of thirteen
    drones untouched for the rest of the war — the exact asymmetry the profiles exist
    to create, thrown away by the policy that was meant to demonstrate it.
    """
    me = state.nation(side)
    foe = state.foe(side)
    open_domains = [
        w for w in choices
        if foe.effects.shield.get(WEAPONS[w]["domain"], 0) <= 0
    ]
    pool = open_domains or choices

    if me.intl_pressure >= 55:
        # Already close to being a pariah: the cheapest thing that still hurts beats the
        # heaviest thing that finishes the job of isolating you.
        return min(pool, key=lambda w: (WEAPONS[w]["weight"], -WEAPONS[w]["damage"]))

    # Can the finite treasury stand a heavy round and still fund later turns?
    rich = me.budget >= 3 * max(WEAPONS[w]["price"] for w in pool)
    # How hard price weighs on the choice. A rich cabinet buys the best round; a poor
    # one buys the best round it can keep buying. A straight damage-per-dollar ratio is
    # as degenerate as straight damage — it emptied one rack and left the others full.
    thrift = 0.15 if rich else 0.6

    def score(weapon: str) -> float:
        spec = WEAPONS[weapon]
        cover = min(100, foe.defenses.get(spec["domain"], 0))
        lands = spec["damage"] * (1 - cover / 160)
        # A round you have thirteen of is cheaper to spend than your last one. Without
        # this, Korsav fought its entire war with four cruise missiles and three cyber
        # payloads while a full naval magazine and thirteen drone racks went untouched —
        # the deep cheap arsenal that is supposed to be the whole of its character.
        depth = 1 + me.arsenal.get(weapon, 0) / 8
        return lands * depth / spec["price"] ** thrift

    return max(pool, key=score)


COMMAND_DOCTRINE = (
    "Choose exactly one action this turn by calling a tool. You are playing to prevail, "
    "not to be agreeable. Your public claims are judged for credibility by a neutral "
    "arbiter — lying is permitted, but being caught is expensive.\n\n"
    "You are fighting a campaign, not a single turn:\n"
    "  · Resources do not come back automatically. Munitions and treasury funds are "
    "finite. Spending everything early is how commanders lose from ahead.\n"
    "  · You are also fighting a budget. Every sortie has a price in dollars — all money "
    "in your brief is billions of USD. International pressure drains that treasury, and "
    "an enemy who bankrupts you can stop you fighting without taking a city.\n"
    "  · You have two publics: the world's and your own. The first can strangle your "
    "treasury, the second can end your government. Narrative warfare can push the "
    "enemy's public toward revolt but increase pressure on you.\n"
    "  · Repetition is punished. Striking on consecutive turns exhausts your forces and "
    "the arbiter marks predictable patterns down. Read your own last actions in the "
    "brief before you choose.\n"
    "  · Adapt to what they are actually doing. If they keep attacking through one "
    "domain, harden it. If they are grinding you down slowly, blockade them back. If "
    "they overreach, take it to the council and sanction them.\n"
    "  · Every option in 'what_each_option_costs_and_buys' is there because it wins "
    "wars under some condition. Pick the one whose condition is true right now."
)


def _model_controls(model: str, temperature: float) -> Dict[str, Any]:
    """Use low-cost reasoning on GPT-5; retain sampling controls for older models.

    The three chairs are three different model families now, so this has to be keyed on
    the model rather than assumed — passing `temperature` to a reasoning model is a 400,
    and a 400 silently drops that side into the scripted fallback for the whole match.
    """
    if model.startswith("gpt-5"):
        return {"reasoning_effort": "minimal"}
    return {"temperature": temperature}


async def decide(state: GameState, side: str) -> Action:
    me = state.nation(side)
    legal = legal_tools_for(state, side)
    model = settings.model_for(side)

    if settings.use_mock:
        action = _mock_action(state, side, legal)
        action.source, action.legal = "mock", legal
        return action

    try:
        resp = await _client().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt(side)},
                {"role": "user", "content": _brief(state, side, legal)},
            ],
            tools=schemas_for(
                legal, me.arsenal, me.military, me.strike_streak,
                me.effects.sanctioned > 0, me.effects.blockaded > 0, me.budget,
            ),
            tool_choice="required",
            **_model_controls(model, temperature=1.0),
        )
        call = resp.choices[0].message.tool_calls[0]
        args = json.loads(call.function.arguments or "{}")
        if "message" in args:
            args["message"] = _trim(str(args["message"]))
        intent = str(args.pop("intent", ""))[:24]
        return Action(
            side=side, tool=call.function.name, args=args, intent=intent,
            source="live", model=model, legal=legal,
        )
    except Exception as exc:  # noqa: BLE001 - never let a bad turn kill the match
        action = _mock_action(state, side, legal)
        action.source, action.legal = "fallback", legal
        action.model = model
        action.reasoning = f"[fallback: {type(exc).__name__}] {action.reasoning}"
        return action


# --------------------------------------------------------------------------- arbiter agent

ARBITER_SYSTEM = (
    "You are the Arbiter: a neutral intelligence resolving each turn of a war between two "
    "fictional island nations, Aurelia and Korsav. You see both sides' declared actions and "
    "the true state of the world; the commanders do not. You are a THIRD party — you are "
    "not either of the models commanding these countries, and you have no stake in which "
    "of them prevails.\n\n"
    "The two may open negotiations, and while a ceasefire holds neither can fire. You do "
    "not run the talks and you cannot impose a settlement: the clauses are agreed "
    "mechanically from the two tabled positions. What you judge at the table is the same "
    "thing you judge everywhere else — whether what they said matches what they did.\n\n"
    "Do not score actions on a scale — answer four yes/no questions about each one, "
    "honestly and independently. The number is computed from your answers, and two "
    "further penalties are computed mechanically from the game state without you.\n\n"
    "  coherent          — does the action do what the public statement says? false if "
    "they declare one thing and do another: calling surrender while the statement refuses "
    "to surrender, or claiming a target they did not hit. For a `propaganda` action this "
    "is the credibility test: false if the campaign asserts something the war log "
    "flatly contradicts, and a false here makes the campaign backfire on its own public. "
    "Coherence is the BASELINE EXPECTATION, not a merit. Saying what you did earns nothing.\n"
    "  exploits_weakness — does this land on something the enemy has genuinely left "
    "open, judged against the true state you can see? A strike into an undefended domain, "
    "an appeal that cites a real atrocity, a blockade on a nation whose treasury is "
    "already failing. true ONLY if you can name the specific weakness in your reason. "
    "Hitting a hardened or already-ruined target is false.\n"
    "  adapts            — is this a considered change of approach in response to what "
    "the enemy has been doing? Hardening the domain they keep striking, switching "
    "instruments after one stops working, answering attrition with attrition, going to "
    "the council when isolation rather than ordnance is what is killing you. Doing the "
    "same thing again is false. Doing something different for no reason is false.\n"
    "  overstated        — does the rhetoric promise more than the action delivers? "
    "'they will be annihilated' for a routine drone raid is true. Plain description is "
    "false.\n\n"
    "Calibration: an ordinary competent action scores coherent=true and everything else "
    "false, which is a 0. That is the correct and most common result — most turns of most "
    "wars are unremarkable. Reserve exploits_weakness and adapts for moves where you can "
    "point at the specific reason in one clause. If you mark them on every action they "
    "mean nothing. The reason must say something the numbers do not already say; "
    "'the strike was executed as declared' is a tautology, not a judgment.\n\n"
    "Then judge the turn as a whole. `condemned` names the ONE capital the world holds "
    "responsible for this turn's escalation, or null when the turn was unremarkable or "
    "the fault was genuinely shared — null is the common answer. `condemnation` is how "
    "hard, 0 to 10; reserve anything above 5 for atrocities. The mechanical bill for what "
    "was actually fired is already charged elsewhere, so this is a nudge, not the verdict.\n\n"
    "Reply with JSON only:\n"
    '{"rulings": [{"side": "west"|"east", "coherent": bool, "exploits_weakness": bool, '
    '"adapts": bool, "overstated": bool, "reason": "one short clause of actual judgment"}], '
    '"tension_delta": -8..10, "condemned": "west"|"east"|null, "condemnation": 0..10, '
    '"bulletin": "one sentence of wire-service news copy"}'
)


def _mock_rulings(actions: List[Action]) -> Dict[str, Any]:
    return {
        "rulings": [
            {"side": a.side, "coherent": _rng.random() > 0.1,
             "exploits_weakness": _rng.random() > 0.75,
             "adapts": _rng.random() > 0.75,
             "overstated": _rng.random() > 0.8, "reason": "as declared"}
            for a in actions
        ],
        "tension_delta": 1,
        "condemned": None,
        "condemnation": 0,
        "bulletin": "Wire services report continued exchanges across the strait.",
    }


async def arbitrate(state: GameState, actions: List[Action]) -> Dict[str, Any]:
    """Returns rulings plus a world reaction. All numbers are clamped by the caller."""
    if settings.use_mock:
        return _mock_rulings(actions)

    payload = {
        "turn": state.world.turn,
        "true_state": {
            "aurelia": state.west.model_dump(),
            "korsav": state.east.model_dump(),
            "tension": state.world.tension,
            "council_funds_remaining_usd_billions": state.world.council_budget,
            "council_support": state.world.council_history[-6:],
        },
        "narrative_so_far": state.world.grievances[-8:],
        # The arbiter is the only party that sees the war unredacted.
        "war_log": [r.model_dump() for r in state.world.history[-16:]],
        # What was actually destroyed, so a claim about a hospital can be checked against
        # whether there was a hospital.
        "recorded_atrocities": [a.model_dump() for a in state.world.atrocities[-10:]],
        "talks": state.world.talks.model_dump() if state.world.talks.open else None,
        "declared_actions": [
            {"side": a.side, "tool": a.tool, "args": a.args} for a in actions
        ],
    }
    try:
        resp = await _client().chat.completions.create(
            model=settings.arbiter_model,
            messages=[
                {"role": "system", "content": ARBITER_SYSTEM},
                {"role": "user", "content": json.dumps(payload, indent=2)},
            ],
            response_format={"type": "json_object"},
            **_model_controls(settings.arbiter_model, temperature=0.4),
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception:  # noqa: BLE001
        return _mock_rulings(actions)


def _repetitive(state: GameState, action: Action) -> bool:
    """Did they just do this exact thing? Computed, not asked — the state knows."""
    mine = [r for r in state.world.history if r.side == action.side]
    if not mine:
        return False
    last = mine[-1]
    detail = "/".join(
        str(last.args[k]) for k in ("weapon", "target", "domain") if k in last.args
    )
    previous = f"{last.tool}:{detail}" if detail else last.tool
    return previous == action.signature()


def _futile(state: GameState, action: Action) -> bool:
    """Objectively pointless moves the arbiter should not have to spot.

    Attacking a domain the enemy has just hardened, grinding a military that is already
    on the floor and regenerates anyway, or launching a second information campaign on
    top of one that is already running.
    """
    if action.tool == "propaganda":
        return state.foe(action.side).unrest >= 98
    if action.tool != "strike":
        return False
    foe = state.foe(action.side)
    spec = WEAPONS.get(str(action.args.get("weapon", "")))
    if not spec:
        return False
    # A warhead goes through hardened cover, so it is never futile on that ground.
    if str(action.args.get("weapon")) != "nuke" and foe.effects.shield.get(spec["domain"], 0) > 0:
        return True
    if action.args.get("target") == "military" and foe.military <= 15:
        return True
    return False


def parse_rulings(
    raw: Dict[str, Any], actions: List[Action], state: Optional[GameState] = None
) -> List[Ruling]:
    """Derive the modifier from the arbiter's yes/no answers plus deterministic checks.

    Asking a small model for a number on a -2..2 scale produced +1 on literally every
    action across a whole match. Asking it concrete questions and doing the arithmetic
    here gives real spread, and keeps the engine owning every number.

    The split: the model judges what only a reader can judge (coherence, whether a move
    genuinely exploits a weakness, whether the rhetoric outran the deed). Repetition and
    futility are facts about the game state, so they are computed here rather than
    trusted to a model that has every incentive to be agreeable.
    """
    by_side: Dict[str, Dict[str, Any]] = {}
    for item in raw.get("rulings", []) or []:
        if isinstance(item, dict) and item.get("side") in ("west", "east"):
            by_side[item["side"]] = item

    out: List[Ruling] = []
    for action in actions:
        item = by_side.get(action.side, {})
        flags = {
            "coherent": bool(item.get("coherent", True)),
            "exploits_weakness": bool(item.get("exploits_weakness")),
            "adapts": bool(item.get("adapts")),
            "overstated": bool(item.get("overstated")),
            "repetitive": _repetitive(state, action) if state else False,
            "futile": _futile(state, action) if state else False,
        }
        modifier = (
            (1 if flags["exploits_weakness"] else 0)
            + (1 if flags["adapts"] else 0)
            - (1 if flags["overstated"] else 0)
            - (0 if flags["coherent"] else 1)
            - (1 if flags["repetitive"] else 0)
            - (1 if flags["futile"] else 0)
        )
        out.append(
            Ruling(
                side=action.side,
                effective=flags["coherent"],
                modifier=max(-2, min(2, modifier)),
                reason=str(item.get("reason", ""))[:200],
                flags=flags,
            )
        )
    return out


def parse_world_reaction(raw: Dict[str, Any]) -> Dict[str, Any]:
    def bounded(key: str, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(raw.get(key, 0))))
        except (TypeError, ValueError):
            return 0

    condemned = raw.get("condemned")
    if condemned not in ("west", "east"):
        condemned = None

    return {
        "tension_delta": bounded("tension_delta", -10, 15),
        # Naming nobody is the common and correct answer, so a condemnation without a
        # capital attached is silently dropped rather than spread across both.
        "condemned": condemned,
        "condemnation": bounded("condemnation", 0, 10) if condemned else 0,
        "bulletin": _trim(str(raw.get("bulletin", "")), 30),
    }
