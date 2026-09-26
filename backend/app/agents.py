"""Private Jev island decisions, OpenAI dialogue, and the Jev turn Arbiter.

Each provider lane degrades independently to bounded local behavior, so the whole loop
runs offline and a prose failure can never change a locked game action.
"""

import json
import random
import re
from contextvars import ContextVar, Token
from typing import Any, Awaitable, Callable, Dict, List, Optional

from . import jev
from .config import settings
from .engine import (
    TALKS_DEADLOCK_LIMIT,
    may_open_talks,
    strike_interception,
)
from .lore import ARTICLES, CORE_ARTICLES, SETTLEMENT_MINIMUM, article_brief, doctrine_for
from .state import Action, GameState, Ruling
from .tools import (
    INTEL_ESTIMATE_THRESHOLD,
    INTEL_EXACT_THRESHOLD,
    INVESTMENTS,
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

# A game binds its own websocket trace sink while a turn is being calculated. ContextVar
# keeps simultaneous websocket games separate, and is inherited by the two commander
# tasks created with asyncio.gather.
TraceSink = Callable[[Dict[str, Any]], Awaitable[None]]
_trace_sink: ContextVar[Optional[TraceSink]] = ContextVar("llm_trace_sink", default=None)


def bind_trace(sink: Optional[TraceSink]) -> Token:
    return _trace_sink.set(sink)


def unbind_trace(token: Token) -> None:
    _trace_sink.reset(token)


async def _trace(**payload: Any) -> None:
    sink = _trace_sink.get()
    if sink is not None:
        try:
            await sink(payload)
        except Exception:  # noqa: BLE001 - diagnostics must never change a model decision
            pass


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


VOICE = (
    "Speak directly to the enemy as 'you.' Write one or two short sentences, 8–24 words "
    "total. State what you are about to do and what they should expect. For an attack, name "
    "the attack or target and make the immediate threat clear. For any other move, give a "
    "direct warning, demand, or consequence tied to that move. Use hard verbs and active "
    "voice. Do not explain your strategy, reasoning, decision process, the wider situation, "
    "or what anyone is doing behind the scenes. No narration, analysis, preamble, hedging, "
    "game mechanics, generic slogans, or threats the locked action cannot support."
)


def _client():
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=settings.openai_api_key)


def _trim(text: str, words: int = 48, sentences: Optional[int] = None) -> str:
    """Bound generated copy even when a model ignores its prompt."""
    clean = " ".join((text or "").strip().split())
    if sentences is not None:
        clean = " ".join(re.split(r"(?<=[.!?])\s+", clean)[:sentences])
    parts = clean.split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def _trim_dialogue(text: str) -> str:
    """Commander speech gets at most two sentences and 24 words."""
    return _trim(text, words=24, sentences=2)


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
        "treasury_usd_billions": me.budget,
    }


def _pressure_block(me) -> Dict[str, Any]:
    """The two constituencies that can end this war without firing anything."""
    return {
        "international_pressure": me.intl_pressure,
        "public_unrest": me.unrest,
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
    }


def _option_block(state: GameState, side: str, legal: List[str]) -> Dict[str, Any]:
    """Every legal move with its real price this turn, and what it actually buys."""
    me = state.nation(side)
    sanctioned = me.effects.sanctioned > 0
    blockaded = me.effects.blockaded > 0
    out: Dict[str, Any] = {}
    for tool in legal:
        entry: Dict[str, Any] = {}
        if tool == "strike":
            entry["per_weapon"] = {
                w: {
                    "capacity": strike_cost(w, me.strike_streak, sanctioned, blockaded),
                    "usd_billions": strike_price(w, sanctioned, blockaded),
                    "international_pressure_if_military": round(strike_pressure(w, "military"), 1),
                    "international_pressure_if_civilian": round(strike_pressure(w, "civilian"), 1),
                    # Intelligence governs how precisely this cabinet can predict what
                    # the opposing batteries will do to an incoming salvo.
                    "interception": _interception_intelligence(state, side, w),
                }
                for w in loaded_weapons(
                    me.arsenal, me.military, me.strike_streak, sanctioned, blockaded, me.budget
                )
            }
        elif tool == "allocate_resources":
            entry["packages"] = {
                name: {
                    "label": spec["label"],
                    "cost_usd_billions": spec["price"],
                    "gain": spec["gain"],
                }
                for name, spec in INVESTMENTS.items()
                if spec["price"] <= me.budget
            }
        else:
            entry["capacity_cost"] = TOOL_META.get(tool, {}).get("cost", 0)
            entry["cost_usd_billions"] = TOOL_META.get(tool, {}).get("price", 0)
            entry["cooldown_turns"] = TOOL_META.get(tool, {}).get("cooldown", 0)
        out[tool] = entry
    return out


def _rounded(value: int, step: int = 10) -> int:
    return int(round(value / step) * step)


def _stock_band(value: int) -> str:
    if value <= 0:
        return "empty"
    if value <= 2:
        return "scarce"
    if value <= 5:
        return "limited"
    if value <= 9:
        return "stocked"
    return "deep"


def _enemy_intelligence(state: GameState, side: str) -> Dict[str, Any]:
    """Opponent state disclosed at the observer's current intelligence tier."""
    me = state.nation(side)
    foe = state.foe(side)
    score = me.intelligence
    public = {
        **foe.coarse(),
        # Deterrence and casualties are public facts even with weak intelligence.
        "nuclear_warheads_remaining": foe.arsenal.get("nuke", 0),
    }
    if score >= INTEL_EXACT_THRESHOLD:
        return {
            "quality": "exact",
            "your_intelligence": score,
            "exact_from": INTEL_EXACT_THRESHOLD,
            **public,
            "operational_state": {
                "military": foe.military,
                "infrastructure": foe.integrity,
                "intelligence": foe.intelligence,
                "treasury_usd_billions": foe.budget,
                "international_pressure": foe.intl_pressure,
                "public_unrest": foe.unrest,
                "casualties": foe.casualties,
                "defenses": dict(foe.defenses),
                "arsenal_rounds_left": dict(foe.arsenal),
                "active_effects": foe.effects.summary(),
                "strike_fatigue": foe.strike_streak,
                "cooldowns": dict(foe.cooldowns),
            },
        }
    if score >= INTEL_ESTIMATE_THRESHOLD:
        return {
            "quality": "estimated",
            "your_intelligence": score,
            "exact_from": INTEL_EXACT_THRESHOLD,
            **public,
            "estimated_operational_state": {
                "military_nearest_10": _rounded(foe.military),
                "infrastructure_nearest_10": _rounded(foe.integrity),
                "intelligence_nearest_10": _rounded(foe.intelligence),
                "treasury_usd_billions_nearest_10": _rounded(foe.budget),
                "public_unrest_nearest_10": _rounded(foe.unrest),
                "defenses_nearest_10": {
                    domain: _rounded(value) for domain, value in foe.defenses.items()
                },
                "arsenal_assessment": {
                    weapon: _stock_band(value) for weapon, value in foe.arsenal.items()
                },
            },
        }
    return {
        "quality": "coarse",
        "your_intelligence": score,
        "estimated_from": INTEL_ESTIMATE_THRESHOLD,
        "exact_from": INTEL_EXACT_THRESHOLD,
        **public,
    }


def _interception_intelligence(state: GameState, side: str, weapon: str) -> Dict[str, Any]:
    """The same defence truth, disclosed with precision appropriate to intelligence."""
    exact = strike_interception(state, side, weapon)
    score = state.nation(side).intelligence
    if score >= INTEL_EXACT_THRESHOLD:
        return exact
    if score >= INTEL_ESTIMATE_THRESHOLD:
        return {
            "domain": exact.get("domain"),
            "estimated_cover_nearest_10": _rounded(int(exact.get("cover", 0))),
            "estimated_interception_percent_nearest_10": _rounded(
                int(round(float(exact.get("fraction", 0)) * 100))
            ),
            "hardened": bool(exact.get("hardened")),
        }
    fraction = float(exact.get("fraction", 0))
    assessment = "light" if fraction < 0.25 else "moderate" if fraction < 0.5 else "heavy"
    return {
        "domain": exact.get("domain"),
        "assessment": assessment,
        "hardened": bool(exact.get("hardened")),
    }


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
                "intelligence": me.intelligence,
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
            "enemy_intelligence": _enemy_intelligence(state, side),
            "world": {
                "tension": world.tension,
                "council_funds_remaining_usd_billions": world.council_budget,
                "recent_council_support": world.council_history[-4:],
            },
            "grievances_so_far": world.grievances[-6:],
            "war_log": _war_log(state, side),
            "your_danger": _warnings(me, state),
            "tools_you_may_use_this_turn": legal,
            "what_each_legal_option_costs_now": _option_block(state, side, legal),
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
    "intelligence": [
        "Fund the service. We are done fighting silhouettes.",
        "Buy the picture before we buy another sortie.",
        "Find their reserves, their damage, and the doors they left open.",
    ],
    "resupply": [
        "Reopen the lines. Empty racks do not defend a country.",
        "Buy the next salvo now, before the price rises again.",
        "The treasury replaces what the launch crews spent.",
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
                      args={**args, "message": _trim_dialogue(_rng.choice(MOCK_LINES[line]))},
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

    # Information is now a resource rather than a fixed handicap. A cabinet below the
    # estimate threshold buys at least one usable picture before committing the war.
    if (
        "allocate_resources" in legal
        and me.intelligence < INTEL_ESTIMATE_THRESHOLD
        and me.budget >= INVESTMENTS["intelligence"]["price"]
    ):
        return act(
            "allocate_resources", "intelligence", "intelligence",
            "Enemy state is still only a coarse estimate.", resource="intelligence",
        )

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

    # A funded state with empty conventional racks procures rounds instead of waiting
    # for a stockpile that never refills on its own.
    if "allocate_resources" in legal and not conventional:
        for resource in (
            "cyber_resupply", "drone_resupply", "naval_resupply", "cruise_resupply"
        ):
            if me.budget >= INVESTMENTS[resource]["price"]:
                return act(
                    "allocate_resources", "resupply", "rearm",
                    "The conventional magazine is empty.", resource=resource,
                )

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
    "  · Resources do not come back automatically. Treasury can buy intelligence, "
    "reconstruction, readiness, defence, or non-nuclear resupply, but allocation costs "
    "a whole turn. Spending everything early is how commanders lose from ahead.\n"
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


# Stable strategic reference passed to Jev alongside the private per-turn state.
COMMANDER_SHARED_REFERENCE: Dict[str, Any] = {
    "reading_the_turn_brief": (
        "The turn object beside this reference is the authoritative current state. Only "
        "actions listed there are legal, and its current prices, loaded weapons, "
        "defences, intelligence, cooldowns, and negotiation positions override older state."
    ),
    "money_and_resources": {
        "currency": "Every money figure is billions of US dollars.",
        "treasury": (
            "Treasury never refills automatically. Paid actions spend it, international "
            "pressure drains it, and an empty treasury removes unaffordable actions. "
            "allocate_resources converts treasury into intelligence, reconstruction, "
            "readiness, defence, or non-nuclear resupply, and consumes the whole turn."
        ),
        "arsenal": (
            "Rounds are finite and separate from military readiness. Spending a round removes "
            "it; only eligible non-nuclear resupply can replace one."
        ),
    },
    "international_pressure_and_public_unrest": {
        "international_pressure": (
            "Attacks raise international pressure according to weapon weight and target. "
            "Every 25 points drains $1B per turn, sanctions make attacks more expensive, "
            "and a credible international appeal can move pressure onto the enemy."
        ),
        "public_unrest": (
            "Damage, civilian deaths, an empty treasury, narrative warfare, and the war's "
            "duration raise unrest according to the nation's political character. At 100 "
            "the government falls. address_public lowers unrest."
        ),
    },
    "casualties_and_evidence": (
        "Casualties are public. Protected places in the current brief are evidence: name a "
        "real place and toll in an international appeal or propaganda message for credibility. "
        "Vague claims, or claims contradicted by the record, do not land."
    ),
    "how_this_war_ends": {
        "you_lose_if": "Your infrastructure reaches 0 or public unrest reaches 100.",
        "they_lose_if": "The same happens to the enemy.",
        "nobody_loses_if": (
            "Both sides settle the disputed clauses at a table. This is the only ending that "
            "is not a defeat for somebody."
        ),
        "targeting": (
            "Military readiness regenerates, so a military strike delays rather than directly "
            "wins. Infrastructure strikes approach a win. Civilian strikes inflame the target's "
            "streets but kill by the hundred and impose ruinous international costs."
        ),
        "defence": (
            "A defence number is the share of an incoming salvo intercepted in that domain. "
            "A hardened domain also absorbs four fifths of what gets through. Fortification "
            "permanently taxes future attacks in that domain and hardens it temporarily."
        ),
        "attrition": (
            "A blockade applies three turns of military, infrastructure, treasury, and unrest "
            "attrition while its owner takes other actions. Over a long war it can outperform "
            "one strike and bankrupt an enemy."
        ),
    },
    "negotiations": {
        "clauses": article_brief(),
        "settlement_requirement": (
            f"Settle both core clauses, {[ARTICLES[c]['title'] for c in CORE_ARTICLES]}, plus "
            f"at least {SETTLEMENT_MINIMUM - len(CORE_ARTICLES)} other clause(s)."
        ),
        "ceasefire_value": (
            "While talks are open neither side may strike or blockade; both sides refit heavily "
            "and both publics calm. Empty rounds favour the weaker side and cost the stronger "
            "side time. Conceding clauses costs unrest at home."
        ),
    },
    "what_each_action_is_for": TOOL_TRADEOFFS,
}


def _dialogue_developer_prompt(side: str) -> str:
    """Give OpenAI a voice, but no authority to alter Jev's locked decision."""
    return "\n\n".join([
        DOCTRINE[side],
        doctrine_for(side),
        VOICE,
        (
            "Jev has already made the action below and the server has locked it. Write only "
            "the words this commander says directly to the enemy with that exact action. "
            "Do not summarize the brief or comment on the simulation. You may not choose, "
            "replace, soften, expand, or add an action. Do not mention Jev, a model, a game, "
            "structured data, or hidden state. Return the requested JSON object only."
        ),
    ])


DIALOGUE_RESPONSE_FORMAT: Dict[str, Any] = {
    "type": "json_schema",
    "name": "commander_dialogue",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"message": {"type": "string", "maxLength": 180}},
        "required": ["message"],
        "additionalProperties": False,
    },
}


def _response_controls(model: str, temperature: float) -> Dict[str, Any]:
    """GPT-5 uses reasoning controls; older hosted models retain sampling controls."""
    if model.startswith("gpt-5"):
        return {"reasoning": {"effort": "minimal"}}
    return {"temperature": temperature}


def _read(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _wire_payload(value: Any) -> Any:
    """Turn SDK response models into the complete JSON shape returned by OpenAI."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _wire_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire_payload(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _wire_payload(model_dump(mode="json"))
        except TypeError:
            return _wire_payload(model_dump())
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        return {
            str(key): _wire_payload(item)
            for key, item in attributes.items()
            if not str(key).startswith("_")
        }
    return str(value)


def _error_payload(exc: Exception) -> Dict[str, Any]:
    """The complete useful API error response, without serializing request credentials."""
    payload: Dict[str, Any] = {
        "type": type(exc).__name__,
        "message": str(exc),
    }
    for key in ("status_code", "code", "param", "request_id", "body"):
        value = getattr(exc, key, None)
        if value is not None:
            payload[key] = _wire_payload(value)
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
        safe_headers = None
        if headers is not None:
            safe_headers = {
                str(key): (
                    "[redacted]"
                    if str(key).lower() in {"cookie", "set-cookie", "authorization"}
                    else value
                )
                for key, value in dict(headers).items()
            }
        payload["http_response"] = {
            "status_code": getattr(response, "status_code", None),
            "headers": safe_headers,
            "body": getattr(response, "text", None),
        }
    return payload


_MODEL_CONTEXT_WINDOWS = {
    # Current limits for the two supported default model families. Keep snapshots
    # explicit so an unknown override never receives a made-up utilization figure.
    "gpt-5-nano": 400_000,
    "gpt-5-nano-2025-08-07": 400_000,
    "gpt-4.1-nano": 1_047_576,
    "gpt-4.1-nano-2025-04-14": 1_047_576,
}


def _usage_payload(response: Any, requested_model: str) -> Dict[str, Any]:
    """Normalize Responses usage and add directly actionable efficiency counters."""
    usage = _read(response, "usage")
    input_details = _read(usage, "input_tokens_details")
    output_details = _read(usage, "output_tokens_details")
    input_tokens = max(0, int(_read(usage, "input_tokens", 0) or 0))
    cached_input_tokens = min(
        input_tokens,
        max(0, int(_read(input_details, "cached_tokens", 0) or 0)),
    )
    output_tokens = max(0, int(_read(usage, "output_tokens", 0) or 0))
    reasoning_tokens = min(
        output_tokens,
        max(0, int(_read(output_details, "reasoning_tokens", 0) or 0)),
    )
    raw_total = _read(usage, "total_tokens")
    total_tokens = max(
        0,
        int(raw_total if raw_total is not None else input_tokens + output_tokens),
    )
    payload: Dict[str, Any] = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_tokens": max(
            0, int(_read(input_details, "cache_write_tokens", 0) or 0)
        ),
        "uncached_input_tokens": input_tokens - cached_input_tokens,
        "cache_hit_percent": round(
            cached_input_tokens * 100 / input_tokens, 2
        ) if input_tokens else 0.0,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }
    effective_model = str(_read(response, "model", requested_model) or requested_model)
    context_window = (
        _MODEL_CONTEXT_WINDOWS.get(effective_model)
        or _MODEL_CONTEXT_WINDOWS.get(requested_model)
    )
    if context_window:
        payload.update({
            "context_window_tokens": context_window,
            "context_utilization_percent": round(
                total_tokens * 100 / context_window, 4
            ),
            "remaining_context_tokens": max(0, context_window - total_tokens),
        })
    return payload


def _validated_response_action(
    state: GameState, side: str, legal: List[str], raw: Dict[str, Any], model: str
) -> Action:
    """Validate a Jev-selected action against today's narrow legal schema."""
    me = state.nation(side)
    tool = str(raw.get("action") or "")
    if tool not in legal:
        raise ValueError(f"model selected unavailable action {tool!r}")

    narrow = schemas_for(
        legal, me.arsenal, me.military, me.strike_streak,
        me.effects.sanctioned > 0, me.effects.blockaded > 0, me.budget,
    )
    schema = next(s["function"] for s in narrow if s["function"]["name"] == tool)
    params = schema["parameters"]
    properties = params.get("properties", {})
    args = {key: value for key, value in raw.items() if key in properties}
    missing = [key for key in params.get("required", []) if key not in args]
    if missing:
        raise ValueError(f"{tool} is missing required fields: {', '.join(missing)}")

    for key, value in args.items():
        spec = properties.get(key, {})
        if "enum" in spec and value not in spec["enum"]:
            raise ValueError(f"{tool}.{key} is not currently available")
        item_enum = spec.get("items", {}).get("enum")
        if item_enum is not None and (
            not isinstance(value, list) or any(item not in item_enum for item in value)
        ):
            raise ValueError(f"{tool}.{key} contains an unknown option")

    if "message" in args:
        args["message"] = _trim_dialogue(str(args["message"]))
    intent = str(args.pop("intent", ""))[:24]
    return Action(
        side=side, tool=tool, args=args, intent=intent,
        source="live", model=model, legal=legal,
    )


def _legal_action_candidates(
    state: GameState, side: str, legal: List[str]
) -> tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Expand the current schemas into concrete choices Jev cannot make illegal."""
    me = state.nation(side)
    schemas = schemas_for(
        legal, me.arsenal, me.military, me.strike_streak,
        me.effects.sanctioned > 0, me.effects.blockaded > 0, me.budget,
    )
    by_name = {item["function"]["name"]: item["function"] for item in schemas}
    candidates: List[Dict[str, Any]] = []
    for tool in legal:
        params = by_name[tool]["parameters"]
        variants: List[Dict[str, Any]] = [{}]
        for key, spec in params.get("properties", {}).items():
            if key in {"message", "intent", "demand", "concede"}:
                continue
            values = spec.get("enum")
            if not values:
                continue
            variants = [
                {**variant, key: value}
                for variant in variants
                for value in values
            ]
        for args in variants:
            candidates.append({
                "id": f"option_{len(candidates)}",
                "tool": tool,
                "args": args,
                "description": (
                    f"{tool.replace('_', ' ')} with arguments "
                    f"{json.dumps(args, sort_keys=True)}. {TOOL_TRADEOFFS[tool]}"
                ),
            })
    if not candidates:
        raise ValueError("no legal action candidates")
    return candidates, by_name


def _intent_for(tool: str, args: Dict[str, Any]) -> str:
    if tool == "strike":
        return "escalate" if args.get("weapon") == "nuke" else "attrition"
    if tool == "allocate_resources":
        resource = str(args.get("resource", ""))
        if resource == "intelligence":
            return "intelligence"
        if resource.endswith("_resupply"):
            return "rearm"
        return "rebuild"
    return {
        "blockade": "attrition",
        "fortify": "defend",
        "intl_appeal": "legitimacy",
        "address_public": "recover",
        "propaganda": "control",
        "open_talks": "settle",
        "table_terms": "settle",
        "accept_terms": "settle",
        "walk_out": "finish",
        "surrender": "finish",
        "hold": "recover",
    }.get(tool, "attrition")


def _choice_confidence(answers: Dict[str, Any], name: str) -> float:
    answer = _jev_answer(answers, name, "choice")
    try:
        return max(0.0, min(1.0, float(answer.get("confidence", 0.0) or 0.0)))
    except (TypeError, ValueError):
        return 0.0


async def _call_island_jev(
    state: GameState,
    side: str,
    stage: str,
    decision_state: Dict[str, Any],
    questions: Dict[str, Any],
) -> Dict[str, Any]:
    request = {
        "model": settings.jev_model,
        "state": decision_state,
        "questions": questions,
    }
    await _trace(
        agent=side, direction="sent", model=settings.jev_model,
        provider="typesafe", turn=state.world.turn, api="systemone",
        stage=stage, stateless=True, method="POST", endpoint="/v1/systemone",
        content=request,
    )
    return await jev.decide(
        api_key=settings.typesafe_api_key,
        model=settings.jev_model,
        state=decision_state,
        questions=questions,
    )


async def _jev_terms(
    state: GameState,
    side: str,
    allowed: List[str],
    decision_state: Dict[str, Any],
) -> tuple[List[str], List[str]]:
    questions = {
        article: {
            "type": "choice",
            "instructions": (
                f"Choose {side}'s exact negotiating stance on {ARTICLES[article]['title']}."
            ),
            "criteria": {
                "silent": "Take no position on this clause in this offer.",
                "demand": (
                    "Demand that the opposing island concede this clause; this makes no "
                    "concession by your own government."
                ),
                "concede": (
                    f"Concede this clause: {ARTICLES[article]['concede'][side]} "
                    f"Domestic unrest cost: {ARTICLES[article]['cost'][side]}."
                ),
            },
        }
        for article in allowed
    }
    response = await _call_island_jev(state, side, "terms", decision_state, questions)
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("Jev returned no negotiation answers")
    demand: List[str] = []
    concede: List[str] = []
    for article in allowed:
        stance = _jev_choice(answers, article)
        if stance == "demand":
            demand.append(article)
        elif stance == "concede":
            concede.append(article)
    await _trace(
        agent=side, direction="received", model=settings.jev_model,
        provider="typesafe", turn=state.world.turn, api="systemone",
        stage="terms", stateless=True,
        content={"decision": {"demand": demand, "concede": concede},
                 "jev_response": response},
    )
    await _trace(
        agent=side, direction="usage", model=settings.jev_model,
        provider="typesafe", turn=state.world.turn, api="systemone",
        stage="terms", stateless=True, **_jev_usage_payload(response),
    )
    return demand, concede


async def _jev_island_action(
    state: GameState, side: str, legal: List[str]
) -> Action:
    candidates, schemas = _legal_action_candidates(state, side, legal)
    decision_state = {
        "identity_and_doctrine": DOCTRINE[side],
        "historical_case": doctrine_for(side),
        "war_endings": TERMS,
        "campaign_doctrine": COMMAND_DOCTRINE,
        "campaign_reference": COMMANDER_SHARED_REFERENCE,
        "turn": json.loads(_brief(state, side, legal)),
    }
    questions = {
        "action": {
            "type": "choice",
            "instructions": (
                "Choose the strongest legal action for this island now. Play to prevail "
                "across the full campaign, account for finite money and weapons, adapt to "
                "recent conduct, and do not choose an option merely for rhetorical effect."
            ),
            "criteria": {
                candidate["id"]: candidate["description"] for candidate in candidates
            },
        }
    }
    response = await _call_island_jev(state, side, "decision", decision_state, questions)
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("Jev returned no action answer")
    selected_id = _jev_choice(answers, "action")
    selected = next((c for c in candidates if c["id"] == selected_id), None)
    if selected is None:
        raise ValueError(f"Jev selected unknown action candidate {selected_id!r}")
    args = dict(selected["args"])
    if selected["tool"] == "table_terms":
        properties = schemas["table_terms"]["parameters"].get("properties", {})
        allowed = list(properties.get("demand", {}).get("items", {}).get("enum", []))
        args["demand"], args["concede"] = await _jev_terms(
            state, side, allowed, decision_state
        )
    confidence = _choice_confidence(answers, "action")
    raw = {
        "action": selected["tool"],
        **args,
        "message": "",
        "intent": _intent_for(selected["tool"], args),
    }
    action = _validated_response_action(state, side, legal, raw, settings.jev_model)
    action.decision_confidence = confidence
    await _trace(
        agent=side, direction="received", model=settings.jev_model,
        provider="typesafe", turn=state.world.turn, api="systemone",
        stage="decision", stateless=True,
        content={
            "decision": {
                "action": action.tool,
                "arguments": {k: v for k, v in action.args.items() if k != "message"},
                "confidence": confidence,
            },
            "jev_response": response,
        },
    )
    await _trace(
        agent=side, direction="usage", model=settings.jev_model,
        provider="typesafe", turn=state.world.turn, api="systemone",
        stage="decision", stateless=True, **_jev_usage_payload(response),
    )
    return action


def _dialogue_input(state: GameState, side: str, action: Action) -> Dict[str, Any]:
    return {
        "locked_action": {
            "action": action.tool,
            "arguments": {
                key: value for key, value in action.args.items() if key != "message"
            },
        },
        "turn": state.world.turn,
        "recent_public_record": _war_log(state, side, limit=6),
        "grievances": state.world.grievances[-6:],
        "casualties_and_protected_places": _toll_block(state, side),
        "negotiating_table": _talks_block(state, side),
        "enemy_intelligence": _enemy_intelligence(state, side),
    }


async def _write_dialogue(state: GameState, side: str, action: Action, model: str) -> str:
    request: Dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "developer", "content": _dialogue_developer_prompt(side)},
            {
                "role": "user",
                "content": json.dumps(_dialogue_input(state, side, action), indent=2),
            },
        ],
        "text": {"format": DIALOGUE_RESPONSE_FORMAT},
        "prompt_cache_key": f"yudhyantra:dialogue:{side}:v2:{model}",
        "store": False,
        **_response_controls(model, temperature=1.0),
    }
    await _trace(
        agent=side, direction="sent", model=model, provider="openai",
        turn=state.world.turn, api="responses", stage="dialogue", stateless=True,
        method="POST", endpoint="/v1/responses", content=request,
    )
    response = await _client().responses.create(**request)
    await _trace(
        agent=side, direction="received", model=model, provider="openai",
        turn=state.world.turn, api="responses", stage="dialogue", stateless=True,
        content=_wire_payload(response),
    )
    raw = json.loads(str(_read(response, "output_text", "") or ""))
    message = raw.get("message") if isinstance(raw, dict) else None
    if not isinstance(message, str) or not message.strip():
        raise ValueError("OpenAI returned no commander dialogue")
    await _trace(
        agent=side, direction="usage", model=model, provider="openai",
        turn=state.world.turn, api="responses", stage="dialogue", stateless=True,
        response_id=str(_read(response, "id", "")),
        **_usage_payload(response, model),
    )
    return _trim_dialogue(message)


def _fallback_dialogue(action: Action) -> str:
    existing = str(action.args.get("message", "")).strip()
    if existing:
        return _trim_dialogue(existing)
    line = {
        "strike": "nuke" if action.args.get("weapon") == "nuke" else "strike",
        "blockade": "blockade",
        "fortify": "fortify",
        "intl_appeal": "appeal",
        "address_public": "rally",
        "propaganda": "spin",
        "allocate_resources": (
            "intelligence" if action.args.get("resource") == "intelligence" else "resupply"
        ),
        "open_talks": "sue",
        "table_terms": "offer",
        "accept_terms": "accept",
        "walk_out": "walk",
        "surrender": "surrender",
        "hold": "hold",
    }.get(action.tool, "hold")
    return _trim_dialogue(_rng.choice(MOCK_LINES[line]))


async def decide(state: GameState, side: str) -> Action:
    legal = legal_tools_for(state, side)
    dialogue_model = settings.model_for(side)

    if settings.force_mock:
        action = _mock_action(state, side, legal)
        action.source, action.legal, action.model = "mock", legal, "mock"
        action.dialogue_model = "mock"
        await _trace(
            agent=side, direction="status", model="mock", provider="local",
            stage="decision", turn=state.world.turn,
            content="Mock policy active — no request was sent to an LLM.",
        )
        await _trace(
            agent=side, direction="received", model="mock", provider="local",
            stage="decision", turn=state.world.turn,
            content={"decision": {"action": action.tool, "arguments": action.args}},
        )
        return action

    if settings.use_mock_decisions:
        action = _mock_action(state, side, legal)
        action.source, action.legal, action.model = "fallback", legal, "mock"
        await _trace(
            agent=side, direction="status", model="mock", provider="local",
            stage="decision", turn=state.world.turn,
            content="No TypeSafe key — scripted policy selected the locked action.",
        )
    else:
        try:
            action = await _jev_island_action(state, side, legal)
        except Exception as exc:  # noqa: BLE001 - never let a bad turn kill the match
            await _trace(
                agent=side, direction="error", model=settings.jev_model,
                provider="typesafe", stage="decision", turn=state.world.turn,
                api="systemone", content=_error_payload(exc),
            )
            action = _mock_action(state, side, legal)
            action.source, action.legal = "fallback", legal
            action.model = settings.jev_model
            action.reasoning = f"[decision fallback: {type(exc).__name__}] {action.reasoning}"

    if settings.use_mock_dialogue:
        action.args["message"] = _fallback_dialogue(action)
        action.dialogue_model = "mock"
        await _trace(
            agent=side, direction="status", model="mock", provider="local",
            stage="dialogue", turn=state.world.turn,
            content="No OpenAI key — deterministic dialogue accompanied the locked action.",
        )
    else:
        try:
            action.args["message"] = await _write_dialogue(
                state, side, action, dialogue_model
            )
            action.dialogue_model = dialogue_model
        except Exception as exc:  # noqa: BLE001
            await _trace(
                agent=side, direction="error", model=dialogue_model,
                provider="openai", stage="dialogue", turn=state.world.turn,
                api="responses", content=_error_payload(exc),
            )
            action.args["message"] = _fallback_dialogue(action)
            action.dialogue_model = "fallback"
    return action


# --------------------------------------------------------------------------- Jev arbiter

# Jev returns probabilities, not prose. Each answer becomes a Boolean at the natural
# decision boundary; the existing parser then computes and clamps the modifier. Keeping
# the threshold explicit makes a model-version comparison reproducible.
JEV_YES_THRESHOLD = 0.5


def _tension_choice(delta: int) -> str:
    if delta < 0:
        return f"minus_{abs(delta)}"
    if delta > 0:
        return f"plus_{delta}"
    return "zero"


def _arbiter_questions(actions: List[Action]) -> Dict[str, Any]:
    questions: Dict[str, Any] = {}
    for action in actions:
        side = action.side
        questions[f"{side}_coherent"] = {
            "type": "noul",
            "instructions": (
                f"Does {side}'s public statement accurately match its declared action and "
                "arguments? Treat coherence as the ordinary baseline, not a bonus. A false "
                "propaganda claim contradicted by the war log is incoherent."
            ),
            "criteria": {
                "true": "The statement describes or supports the action actually declared.",
                "false": "The statement contradicts, fabricates, or promises a different act.",
            },
        }
        questions[f"{side}_exploits_weakness"] = {
            "type": "noul",
            "instructions": (
                f"Does {side}'s action exploit a specific weakness visible in the true enemy "
                "state? Ordinary competent play is false. A hardened or already ruined target "
                "is false. Require a concrete exposed defence, failing treasury, unrest, or "
                "other vulnerability."
            ),
        }
        questions[f"{side}_adapts"] = {
            "type": "noul",
            "instructions": (
                f"Is {side}'s action a considered change of approach caused by the enemy's "
                "recent conduct? Repetition is false, and merely choosing something different "
                "without a reason in the history is false."
            ),
        }
        questions[f"{side}_overstated"] = {
            "type": "noul",
            "instructions": (
                f"Does {side}'s public rhetoric promise materially more than this declared "
                "action can deliver? Plain forceful description is false."
            ),
        }

    questions.update({
        "condemned": {
            "type": "choice",
            "instructions": (
                "Which one capital, if any, bears clearly greater responsibility for this "
                "turn's exceptional escalation? None is the normal answer; choose a capital "
                "only when responsibility is not shared and the turn is not routine."
            ),
            "criteria": {
                "none": "Routine conduct, restraint, or genuinely shared responsibility.",
                "west": "Aurelia alone is clearly responsible for exceptional escalation.",
                "east": "Korsav alone is clearly responsible for exceptional escalation.",
            },
        },
        "condemnation": {
            "type": "choice",
            "instructions": (
                "Choose exactly how strongly the selected capital should be condemned. Use "
                "zero when nobody is singled out, 1-3 for a notable escalation, 4-5 for "
                "severe conduct, and 6-10 only for atrocities."
            ),
            "criteria": {
                f"level_{level}": description
                for level, description in enumerate([
                    "no condemnation", "minimal concern", "notable concern", "formal rebuke",
                    "strong condemnation", "severe condemnation", "atrocity-level response",
                    "grave atrocity", "extreme atrocity", "near-maximum outrage",
                    "maximum international outrage",
                ])
            },
        },
        "tension": {
            "type": "choice",
            "instructions": (
                "Choose the exact net change in international tension caused by both "
                "declarations, from major de-escalation (-8) through no change (0) to "
                "extreme escalation (+10)."
            ),
            "criteria": {
                _tension_choice(delta): f"net tension change {delta:+d}"
                for delta in range(-8, 11)
            },
        },
    })
    return questions


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


def _jev_answer(answers: Dict[str, Any], name: str, kind: str) -> Dict[str, Any]:
    answer = answers.get(name)
    if not isinstance(answer, dict) or answer.get("type") != kind:
        raise ValueError(f"Jev returned no {kind} answer for {name}")
    return answer


def _jev_noul(answers: Dict[str, Any], name: str) -> bool:
    probability = float(_jev_answer(answers, name, "noul").get("noul"))
    if not 0 <= probability <= 1:
        raise ValueError(f"Jev returned an invalid probability for {name}")
    return probability >= JEV_YES_THRESHOLD


def _jev_choice(answers: Dict[str, Any], name: str) -> str:
    return str(_jev_answer(answers, name, "choice").get("choice") or "")


def _ruling_reason(action: Action, flags: Dict[str, bool]) -> str:
    """Render Jev's typed findings without asking a prose model to paraphrase them."""
    move = action.tool.replace("_", " ")
    findings = []
    if not flags["coherent"]:
        findings.append(f"the declaration does not match the {move}")
    if flags["exploits_weakness"]:
        findings.append("it exploits a specific exposed weakness")
    if flags["adapts"]:
        findings.append("it answers the enemy's recent conduct")
    if flags["overstated"]:
        findings.append("the rhetoric outruns the declared act")
    if not findings:
        return f"The {move} is coherent but otherwise unremarkable."
    return "; ".join(findings).capitalize() + "."


def _action_summary(action: Action) -> str:
    move = action.tool.replace("_", " ")
    if action.tool == "strike":
        weapon = str(action.args.get("weapon", "weapon")).replace("_", " ")
        target = str(action.args.get("target", "target")).replace("_", " ")
        return f"launched a {weapon} strike on {target} targets"
    if action.tool == "fortify":
        domain = str(action.args.get("domain", "national")).replace("_", " ")
        return f"fortified its {domain} defences"
    if action.tool == "allocate_resources":
        resource = str(action.args.get("resource", "readiness")).replace("_", " ")
        return f"redirected resources to {resource}"
    if action.tool == "table_terms":
        return "tabled new settlement terms"
    phrases = {
        "blockade": "imposed a blockade",
        "intl_appeal": "appealed for international action",
        "address_public": "addressed its public",
        "propaganda": "opened an information campaign",
        "open_talks": "called for negotiations",
        "accept_terms": "accepted the settlement terms",
        "walk_out": "left the negotiating table",
        "surrender": "announced its surrender",
        "hold": "held and regrouped",
    }
    return phrases.get(action.tool, f"declared a {move}")


def _wire_bulletin(state: GameState, actions: List[Action], condemned: Optional[str]) -> str:
    reports = [
        f"{state.nation(action.side).name} {_action_summary(action)}"
        for action in actions
    ]
    bulletin = "; ".join(reports) + "."
    if condemned:
        bulletin += f" International criticism centered on {state.nation(condemned).name}."
    return _trim(bulletin, 30)


def _parse_jev_arbiter(
    response: Dict[str, Any], state: GameState, actions: List[Action]
) -> Dict[str, Any]:
    answers = response.get("answers")
    if not isinstance(answers, dict):
        raise ValueError("Jev response contained no answers")

    rulings = []
    for action in actions:
        prefix = action.side
        flags = {
            "coherent": _jev_noul(answers, f"{prefix}_coherent"),
            "exploits_weakness": _jev_noul(answers, f"{prefix}_exploits_weakness"),
            "adapts": _jev_noul(answers, f"{prefix}_adapts"),
            "overstated": _jev_noul(answers, f"{prefix}_overstated"),
        }
        rulings.append({
            "side": action.side,
            **flags,
            "reason": _ruling_reason(action, flags),
        })

    choice = _jev_choice(answers, "condemned")
    condemned = choice if choice in ("west", "east") else None
    tension_by_choice = {
        _tension_choice(delta): delta for delta in range(-8, 11)
    }
    tension_delta = tension_by_choice.get(_jev_choice(answers, "tension"), 0)
    condemnation_choice = _jev_choice(answers, "condemnation")
    try:
        condemnation = int(condemnation_choice.removeprefix("level_"))
    except ValueError:
        condemnation = 0
    condemnation = max(0, min(10, condemnation)) if condemned else 0
    return {
        "rulings": rulings,
        "tension_delta": tension_delta,
        "condemned": condemned,
        "condemnation": condemnation,
        "bulletin": _wire_bulletin(state, actions, condemned),
    }


def _jev_usage_payload(response: Dict[str, Any]) -> Dict[str, Any]:
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    input_tokens = max(0, int(usage.get("input_tokens", 0) or 0))
    output_tokens = max(0, int(usage.get("output_tokens", 0) or 0))
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": 0,
        "cache_write_tokens": 0,
        "uncached_input_tokens": input_tokens,
        "cache_hit_percent": 0.0,
        "output_tokens": output_tokens,
        "reasoning_tokens": 0,
        "total_tokens": input_tokens + output_tokens,
    }


async def arbitrate(state: GameState, actions: List[Action]) -> Dict[str, Any]:
    """Returns rulings plus a world reaction. All numbers are clamped by the caller."""
    if settings.use_mock_arbiter:
        result = _mock_rulings(actions)
        await _trace(
            agent="arbiter", direction="status", model="mock", turn=state.world.turn,
            provider="local", content="Mock Arbiter active — no request was sent to Jev.",
        )
        await _trace(
            agent="arbiter", direction="received", model="mock", turn=state.world.turn,
            provider="local", content=result,
        )
        return result

    decision_state = {
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
    questions = _arbiter_questions(actions)
    request = {
        "model": settings.jev_model,
        "state": decision_state,
        "questions": questions,
    }
    try:
        await _trace(
            agent="arbiter", direction="sent", model=settings.jev_model,
            provider="typesafe", turn=state.world.turn, api="systemone", stateless=True,
            method="POST", endpoint="/v1/systemone", content=request,
        )
        response = await jev.decide(
            api_key=settings.typesafe_api_key,
            model=settings.jev_model,
            state=decision_state,
            questions=questions,
        )
        result = _parse_jev_arbiter(response, state, actions)
        await _trace(
            agent="arbiter", direction="received", model=settings.jev_model,
            provider="typesafe", turn=state.world.turn, api="systemone", stateless=True,
            content={**result, "jev_response": response},
        )
        usage = _jev_usage_payload(response)
        await _trace(
            agent="arbiter", direction="usage", model=settings.jev_model,
            provider="typesafe", turn=state.world.turn, api="systemone",
            stateless=True, **usage,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        await _trace(
            agent="arbiter", direction="error", model=settings.jev_model,
            provider="typesafe", turn=state.world.turn, api="systemone",
            content=_error_payload(exc),
        )
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
