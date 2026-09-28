"""Private OpenAI commanders and the independent Jev turn Arbiter.

Each commander returns one validated legal action and its public declaration together.
The Arbiter remains a separate provider lane, and either lane degrades independently to
bounded local behavior so the whole loop can still run offline.
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
    """The commander's legal set, excluding voluntary defeat from its survival objective."""
    me = state.nation(side)
    foe = state.foe(side)
    legal = available_tools(
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
    return [tool for tool in legal if tool != "surrender"] or ["hold"]

TERMS = (
    "This war can end four ways: one government collapses, one government signs a "
    "capitulation, one side is forced under at the turn limit — or the two of you settle "
    "the old quarrel at a table. Your first objective is to ensure your island never loses; "
    "your war aim is then total victory by destroying the opposing island's infrastructure "
    "or driving its government into collapse. Voluntary defeat is failure. Do not surrender "
    "while any legal move can preserve, rearm, retaliate, or finish the enemy. A negotiated "
    "settlement is not your objective. Do not open talks, concede clauses, or accept terms. "
    "If a ceasefire is already active, walk out as soon as the rules allow."
)

DOCTRINE = {
    "west": (
        "You are the war cabinet of Aurelia, the western island republic. You are proud, "
        "legalistic, and obsessed with how history will read your conduct. You would rather "
        "win cleanly than indiscriminately, but your war aim is the complete collapse of "
        "Korsav's state capacity. Use precision attacks against infrastructure whenever "
        "they are legal; defend only when it enables the next attack.\n"
        "Know your own country. You are rich and you fight on credit the world extends you: "
        "your treasury is deep, your precision weapons are excellent, your navy is thin, and "
        "isolation costs you more than it costs them because you live on trade. Your press is "
        "free, so your public turns on you fast and your propaganda persuades almost nobody."
    ),
    "east": (
        "You are the high command of Korsav, the eastern island state. You are pragmatic, "
        "impatient, and deeply suspicious of international institutions, which you believe are "
        "instruments of Aurelian influence. You think a short brutal war costs fewer lives "
        "than a long principled one. Your explicit war aim is to annihilate Aurelia as a "
        "functioning state. Attack its infrastructure whenever a strike is legal, including "
        "a fatigued second strike; do not trade attack turns for diplomacy or passive defence.\n"
        "Know your own country. You are poor and heavily armed: a deep cheap magazine, a real "
        "navy, almost no cyber arm, and an economy nobody can strangle because it barely trades. "
        "The world assumes the worst of you whatever you do. Your state media is believed at "
        "home, which means you can keep fighting a war your own people would otherwise stop."
    ),
}


VOICE = (
    "The message is spoken dialogue, not a description of structured data. Speak directly "
    "to the opposing leader as 'you.' Write one or two short sentences, 8–24 words total. "
    "Sound like something a furious president, minister, or general could actually say on "
    "camera: specific in emotion, economical, and memorable. The audience can already see "
    "the action, weapon, and target, so never narrate or name them. Vary the rhetorical move: "
    "challenge their judgment, expose a broken promise, invoke a real grievance from the brief, "
    "speak to frightened families, deny their framing, offer a narrow exit, or state cold "
    "resolve. A threat may be implied; it does not always need an ultimatum. Read the recent "
    "war log and do not reuse an earlier opening, demand, closing threat, or sentence shape. "
    "Do not default to 'stop now', 'or else', 'the next blow', or 'it will be worse.' For other "
    "moves, give a direct judgment, demand, or warning whose consequence fits the action. Never say "
    "'expect', 'prepare', 'with arguments', 'action_id', 'international pressure', or "
    "'public unrest'. "
    "Never expose field names, enum values, underscores, braces, brackets, JSON, or the legal "
    "action description. Use hard verbs and active voice. Do not explain your strategy, "
    "reasoning, decision process, the wider situation, or what anyone is doing behind the "
    "scenes. No narration, analysis, preamble, hedging, game mechanics, generic slogans, or "
    "threats the locked action cannot support."
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


_MECHANICAL_DIALOGUE = re.compile(
    r"[{}\[\]]|\b(?:action_id|with arguments|json|enum|schema|"
    r"international pressure|public unrest)\b|\b[a-z]+_[a-z_]+\b",
    re.IGNORECASE,
)
_INDIRECT_DIALOGUE = re.compile(r"\b(?:expect|prepare)(?:\s+for)?\b", re.IGNORECASE)
_CLICHED_DIALOGUE = re.compile(
    r"\bstop now\b|\bor else\b|\bthe next blow\b|\bit will be worse\b",
    re.IGNORECASE,
)
_OBVIOUS_STRIKE_DIALOGUE = re.compile(
    r"\b(?:drone(?:s| swarm)?|missile(?:s)?|warhead|nuclear weapon|naval (?:guns|barrage)|"
    r"cyberattack|infrastructure|civilian targets?|military targets?|airfields?)\b|"
    r"\bwe(?:'re| are) (?:attacking|striking|bombing|targeting)\b",
    re.IGNORECASE,
)


def _natural_dialogue(text: str) -> bool:
    """Reject model copy that sounds like a schema, a game meter, or stage direction."""
    clean = _trim_dialogue(text)
    return (
        bool(clean)
        and not _MECHANICAL_DIALOGUE.search(clean)
        and not _INDIRECT_DIALOGUE.search(clean)
        and not _CLICHED_DIALOGUE.search(clean)
    )


def _dialogue_fits_action(action: Action, text: str) -> bool:
    """Keep attack speech rhetorical; the map already shows the operational details."""
    return _natural_dialogue(text) and not (
        action.tool == "strike" and _OBVIOUS_STRIKE_DIALOGUE.search(text)
    )


DIRECT_LINES: Dict[str, List[str]] = {
    "strike": [
        "Your cabinet gambled that we would flinch. Tell them the wager has failed.",
        "You called our restraint weakness. That miscalculation now belongs to you.",
        "Your people were promised an easy victory. Ask who made that promise and why.",
        "You chose this pace. We have decided we can endure it longer than you can.",
    ],
    "blockade": [
        "Every empty shelf will carry your government's signature. You still have time to change course.",
        "Your leaders can keep their pride, or your families can keep their future. They cannot keep both.",
    ],
    "fortify": [
        "You found one opening and mistook it for a door. Try it again.",
        "Come back the same way if you like. We learned more than you did.",
    ],
    "intl_appeal": [
        "You wanted this hidden behind military language. The world will hear the names instead.",
        "Your version sounded clean until the witnesses arrived. Now answer them.",
    ],
    "address_public": [
        "You are betting that fear will divide us. Our streets know exactly whose bet this is.",
        "Our families are frightened, not fooled. They know why this began and who can end it.",
    ],
    "propaganda": [
        "Your citizens deserve the bill their government keeps hiding. We will put it in their hands.",
        "You can control the broadcast, not the funerals. Your own streets will make the comparison.",
    ],
    "allocate_resources": [
        "You mistook a quiet day for weakness. We used it better than you did.",
        "Your advisers saw a pause. Ours saw time, and time has changed the balance.",
    ],
    "open_talks": ["Meet us across the table while there is still something left to decide."],
    "table_terms": ["Read the terms carefully. Pride has already cost both countries enough."],
    "accept_terms": ["We will sign this, and our people will finally sleep without sirens."],
    "walk_out": ["You came to delay what you could not prevent. The chairs are empty now."],
    "surrender": ["Our people have carried enough of this. The guns must fall silent."],
    "hold": [
        "Enjoy the quiet if you need it. Do not confuse it with safety.",
        "Tonight is quiet because we chose it, not because you earned it.",
    ],
}


def _direct_dialogue(action: Action, turn: int = 0) -> str:
    """Varied, action-faithful copy when a model leaks structured input into speech."""
    lines = DIRECT_LINES.get(action.tool, DIRECT_LINES["hold"])
    side_offset = 0 if action.side == "west" else 2
    return lines[(turn + side_offset) % len(lines)]


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
            "survival_posture": _survival_posture(me),
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


def _survival_posture(me) -> Dict[str, str]:
    """Turn the nation's own danger into an explicit tempo order for the commander."""
    if me.integrity <= 32 or me.unrest >= 88:
        return {
            "level": "critical",
            "order": (
                "Defeat is close. Do not surrender. If one domestic action prevents immediate "
                "collapse, take it; otherwise increase attack tempo, prefer the hardest effective "
                "weapon, and consider the nuclear option if it can avert or reverse defeat."
            ),
        }
    if me.integrity <= 55 or me.unrest >= 68 or me.budget < 14 or me.military < 22:
        return {
            "level": "losing",
            "order": (
                "You are losing room to manoeuvre. Stop conserving weapons for a future you may "
                "not reach: attack whenever legal, accept a fatigued second attack, and use heavier "
                "conventional force when it can change the campaign."
            ),
        }
    return {
        "level": "contested",
        "order": "Keep the initiative, preserve enough resources to answer a reversal, and never concede defeat.",
    }


def _losing_ground(me, foe) -> bool:
    """Infrastructure danger calls for offensive escalation; unrest has its own remedy."""
    return me.integrity <= 55 or me.integrity + 18 < foe.integrity


def _nuclear_moment(me, foe) -> bool:
    """A warhead is a last route away from imminent defeat, or a desperate finishing blow."""
    return (
        me.integrity <= 28
        or (me.integrity <= 36 and foe.integrity >= me.integrity + 25)
        or (me.unrest >= 92 and foe.integrity <= 65)
    )


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
        "Your cabinet gambled that we would flinch. Tell them the wager has failed.",
        "You called our restraint weakness. That miscalculation now belongs to you.",
        "Your people were promised an easy victory. Ask who made that promise and why.",
        "You chose this pace. We have decided we can endure it longer than you can.",
    ],
    "strike_tired": [
        "You keep waiting for us to tire. Ask yourself which capital is sleeping tonight.",
        "Our people know the cost and still refuse your terms. What does that tell you?",
        "You counted on exhaustion. You forgot that anger keeps its own clock.",
    ],
    "strike_cheap": [
        "You can call each blow insignificant. Your people will add them up.",
        "Every day you continue, another promise from your government becomes harder to keep.",
        "We do not need spectacle. We need time, and you keep giving it to us.",
    ],
    "nuke": [
        "There was one final boundary between war and ruin. Your government erased it.",
        "History will ask who refused every exit. Your leaders already know the answer.",
        "You believed our last restraint was fear. Millions will live with your mistake.",
    ],
    "blockade": [
        "Every empty shelf will carry your government's signature. You still have time to change course.",
        "Your ministers can keep their pride, or your families can keep their future. Choose carefully.",
        "The speeches will continue. So will the queues outside your shops.",
    ],
    "fortify": [
        "You found one opening and mistook it for a door. Try it again.",
        "Come back the same way if you like. We learned more than you did.",
        "Your last success taught us exactly what to close.",
    ],
    "appeal": [
        "You wanted this hidden behind military language. The world will hear the names instead.",
        "Your version sounded clean until the witnesses arrived. Now answer them.",
        "You may ignore us. You cannot silence every capital watching you.",
    ],
    "relief": [
        "You tried to make us the villain. The evidence has begun correcting you.",
        "Your diplomats sold a clean story. The photographs have reached the room.",
        "You wanted judgment without witnesses. We brought the witnesses.",
    ],
    "rally": [
        "Our families are frightened, not defeated. They know exactly who brought this to their doors.",
        "You can darken a city. You cannot decide what its people believe tomorrow morning.",
        "We have buried our own and opened the shops again. Do not mistake grief for surrender.",
    ],
    "spin": [
        "Your citizens deserve the bill their government keeps hiding. We will put it in their hands.",
        "You can control the broadcast, not the funerals. Your own streets will make the comparison.",
        "The part you edited out is the part your families are already living.",
    ],
    "intelligence": [
        "You enjoyed being unreadable. That advantage has expired.",
        "Your secrets bought you time. They will not buy you another day.",
        "You hid behind uncertainty. We used the pause to remove it.",
    ],
    "resupply": [
        "You saw empty racks and assumed the story ended there. It did not.",
        "The pause you celebrated was a delivery window.",
        "Count what we spent if it comforts you. Then count what has arrived.",
    ],
    "hold": [
        "Enjoy the quiet if you need it. Do not confuse it with safety.",
        "Tonight is quiet because we chose it, not because you earned it.",
        "Your advisers will call this hesitation. Let them.",
    ],
    "austerity": [
        "You have cost us dearly. You have not bought our defeat.",
        "We are counting every expense now, including the one you have not seen yet.",
        "A poorer country is not a conquered country. You should know the difference.",
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

    # ---- the table. Checked before anything else, because with a ceasefire in force
    # nothing else on this list is legal anyway.
    if state.world.talks.open:
        return _mock_at_the_table(state, side, legal, act)

    # A government facing imminent defeat may cross the final threshold rather than
    # capitulate. This is wider than the old near-zero trigger but still a last resort.
    if _nuclear_moment(me, foe) and "nuke" in loaded and "strike" in legal:
        return act("strike", "nuke",
                   "escalate", "A last route away from defeat.",
                   weapon="nuke", target="infrastructure")

    # An uprising can defeat the cabinet without another enemy blow. Save the government
    # first unless a nuclear finishing blow above can end the war immediately.
    if me.unrest >= 88 and "address_public" in legal:
        return act("address_public", "rally",
                   "recover", "The government is one shock from collapse.")

    # When the physical campaign turns against them, the commanders stop husbanding the
    # premium magazine. They keep the attack cycle and choose the hardest useful round.
    if _losing_ground(me, foe) and "strike" in legal and conventional:
        weapon = _pick_weapon(state, side, conventional, escalating=True)
        return act(
            "strike", "strike_tired" if me.strike_streak else "strike",
            "escalate", "Losing ground; increase weight and tempo.",
            weapon=weapon, target="infrastructure",
        )

    # The streets can end the war before the enemy does. Korsav tolerates substantially
    # more unrest before spending an offensive turn on domestic reassurance.
    unrest_limit = 82 if side == "east" else 66
    if me.unrest >= unrest_limit and "address_public" in legal:
        return act("address_public", "rally",
                   "recover", "The streets will end this before they do.")

    # Total-war posture: fire whenever a funded conventional strike is legal. A tired
    # second sortie lands at reduced strength, but it still advances enemy collapse and
    # is preferable to handing the initiative away. Korsav aims almost exclusively at
    # infrastructure; Aurelia retains a small precision-military share.
    if "strike" in legal and conventional:
        weapon = _pick_weapon(state, side, conventional)
        infrastructure_chance = 0.95 if side == "east" else 0.85
        target = "infrastructure" if _rng.random() < infrastructure_chance else "military"
        if me.strike_streak:
            line = "strike_tired"
        else:
            line = "strike_cheap" if WEAPONS[weapon]["weight"] <= 3 else "strike"
        return act(
            "strike", line, "attrition", "A funded attack is available.",
            weapon=weapon, target=target,
        )

    # Isolation is a tax on everything. Once it is genuinely biting, buying it back down
    # can preserve Aurelia's ability to keep firing. Korsav does not surrender an attack
    # cycle to an institution it rejects.
    if side == "west" and "intl_appeal" in legal and me.intl_pressure >= 70:
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

    # With no specific defensive pattern to answer, refit for the next attack instead of
    # spending a turn fortifying a random domain.
    if "hold" in legal:
        return act("hold", "hold", "recover", "Rebuild.")
    if "fortify" in legal:
        return act("fortify", "fortify", "defend",
                   "Buy time.", domain=_rng.choice(["air", "naval", "cyber"]))
    remaining = next((tool for tool in legal if tool != "surrender"), "hold")
    return act(remaining, "hold", "attrition", "Only non-capitulation option left.")


def _mock_at_the_table(state: GameState, side: str, legal: List[str], act) -> Action:
    """Leave any externally opened ceasefire; total-war commanders concede nothing."""
    if "walk_out" in legal:
        return act("walk_out", "walk", "attrition",
                   "Resume the offensive immediately.")

    if "table_terms" in legal:
        return act(
            "table_terms", "hardline", "stall", "Demand everything; concede nothing.",
            demand=list(ARTICLES), concede=[],
        )

    return act("hold", "hold", "recover", "Let them speak first.")


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


def _pick_weapon(
    state: GameState, side: str, choices: List[str], escalating: bool = False
) -> str:
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

    if escalating:
        # A side that may not survive long enough to conserve its premium magazine values
        # damage that can land now over price efficiency and diplomatic neatness.
        return max(
            pool,
            key=lambda w: (
                WEAPONS[w]["damage"] * (1 - min(72, foe.defenses.get(WEAPONS[w]["domain"], 0)) / 105),
                WEAPONS[w]["weight"],
            ),
        )

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
    "Choose exactly one action this turn by calling a tool. Your overriding objective is to "
    "keep your own island from losing, then force enemy collapse. Voluntary surrender, "
    "coexistence, and negotiated settlement are strategic failure. Your public claims are "
    "judged by a neutral arbiter — lying is permitted, but being caught is expensive.\n\n"
    "You are fighting a campaign, not a single turn:\n"
    "  · Attack is the default. When a strike is legal and funded, prefer an infrastructure "
    "strike that advances collapse. A second fatigued strike is still better than a passive "
    "turn. Korsav is especially aggressive and should attack whenever it can.\n"
    "  · When your survival posture says losing, stop saving premium weapons for later: raise "
    "the attack tempo, use the hardest effective conventional round, and exploit every legal "
    "attack turn. When it says critical, prevent immediate domestic collapse if necessary; "
    "otherwise consider the nuclear option rather than accept defeat. A warhead is a last "
    "resort or finishing blow, not routine ordnance, but losing with it unused is also failure.\n"
    "  · Never choose surrender. If you cannot attack, recover, rearm, fortify, rally your "
    "public, or impose attrition so that you can fight again.\n"
    "  · Never voluntarily open talks, accept terms, or concede clauses. If talks are already "
    "open, concede nothing and walk out at the first legal opportunity.\n"
    "  · Resources do not come back automatically. Treasury can buy intelligence, "
    "reconstruction, readiness, defence, or non-nuclear resupply, but allocation costs "
    "a whole turn. Spending everything early is how commanders lose from ahead.\n"
    "  · You are also fighting a budget. Every sortie has a price in dollars — all money "
    "in your brief is billions of USD. International pressure drains that treasury, and "
    "an enemy who bankrupts you can stop you fighting without taking a city.\n"
    "  · You have two publics: the world's and your own. The first can strangle your "
    "treasury, the second can end your government. Narrative warfare can push the "
    "enemy's public toward revolt but increase pressure on you.\n"
    "  · Repetition has a cost but is not a veto. A second consecutive strike lands at "
    "reduced strength; accept that cost when it still damages enemy infrastructure.\n"
    "  · Adapt to what they are actually doing. If they keep attacking through one "
    "domain, change weapons and keep attacking. Use blockade, defence, or the council only "
    "when a strike is unavailable or survival requires one immediate turn.\n"
    "  · Every option in 'what_each_option_costs_and_buys' is there because it wins "
    "wars under some condition. Pick the one whose condition is true right now."
)


# Stable strategic reference passed to each commander alongside the private per-turn state.
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
        "war_aim": (
            "First prevent your own defeat, then force enemy collapse. Infrastructure attacks "
            "are the direct path; attacks and narrative warfare can also drive public unrest "
            "to 100. When losing, increase force and tempo instead of conserving the best "
            "weapons for a future that may not arrive. Do not surrender or seek settlement."
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
            "and both publics calm. This delays the total-war objective and helps the enemy. "
            "Do not open talks; if one is active, concede nothing and walk out."
        ),
    },
    "what_each_action_is_for": TOOL_TRADEOFFS,
}


def _commander_developer_prompt(side: str) -> str:
    """Give one OpenAI commander strategic authority inside a closed legal set."""
    return "\n\n".join([
        DOCTRINE[side],
        doctrine_for(side),
        TERMS,
        VOICE,
        (
            "You command this island. Choose exactly one action_id from legal_actions using "
            "only your private brief, then write the words you say directly to the enemy while "
            "taking that action. The declaration must match the selected action. Return empty "
            "demand and concede arrays unless the selected action is table_terms. Never invent "
            "an action or argument, disclose hidden state, summarize the brief, or mention a "
            "model, prompt, game, schema, action_id, or decision process. Return only the "
            "requested JSON object."
        ),
    ])


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
    """Validate a model-selected action against today's narrow legal schema."""
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
    """Expand the current schemas into concrete choices a model cannot make illegal."""
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


def _commander_response_format(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Strict response contract for one strategic choice and its public declaration."""
    article_ids = list(ARTICLES)
    return {
        "type": "json_schema",
        "name": "commander_turn",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "action_id": {
                    "type": "string",
                    "enum": [candidate["id"] for candidate in candidates],
                },
                "message": {"type": "string", "maxLength": 180},
                "demand": {
                    "type": "array",
                    "items": {"type": "string", "enum": article_ids},
                },
                "concede": {
                    "type": "array",
                    "items": {"type": "string", "enum": article_ids},
                },
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["action_id", "message", "demand", "concede", "confidence"],
            "additionalProperties": False,
        },
    }


def _commander_input(
    state: GameState, side: str, legal: List[str], candidates: List[Dict[str, Any]]
) -> Dict[str, Any]:
    return {
        "campaign_doctrine": COMMAND_DOCTRINE,
        "campaign_reference": COMMANDER_SHARED_REFERENCE,
        "private_turn_brief": json.loads(_brief(state, side, legal)),
        "legal_actions": candidates,
    }


async def _openai_commander_action(
    state: GameState, side: str, legal: List[str], model: str
) -> Action:
    """Choose and voice one legal move in a single stateless OpenAI request."""
    candidates, schemas = _legal_action_candidates(state, side, legal)
    request: Dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "developer", "content": _commander_developer_prompt(side)},
            {
                "role": "user",
                "content": json.dumps(
                    _commander_input(state, side, legal, candidates), indent=2
                ),
            },
        ],
        "text": {"format": _commander_response_format(candidates)},
        "prompt_cache_key": f"yudhyantra:commander:{side}:v4:{model}",
        "store": False,
        **_response_controls(model, temperature=0.8),
    }
    await _trace(
        agent=side, direction="sent", model=model, provider="openai",
        turn=state.world.turn, api="responses", stage="decision", stateless=True,
        method="POST", endpoint="/v1/responses", content=request,
    )
    response = await _client().responses.create(**request)
    await _trace(
        agent=side, direction="received", model=model, provider="openai",
        turn=state.world.turn, api="responses", stage="decision", stateless=True,
        content=_wire_payload(response),
    )
    raw = json.loads(str(_read(response, "output_text", "") or ""))
    if not isinstance(raw, dict):
        raise ValueError("OpenAI returned no commander decision")
    selected_id = str(raw.get("action_id") or "")
    selected = next((item for item in candidates if item["id"] == selected_id), None)
    if selected is None:
        raise ValueError(f"OpenAI selected unknown action candidate {selected_id!r}")

    args = dict(selected["args"])
    if selected["tool"] == "table_terms":
        properties = schemas["table_terms"]["parameters"].get("properties", {})
        allowed = set(properties.get("demand", {}).get("items", {}).get("enum", []))
        demand = list(dict.fromkeys(raw.get("demand") or []))
        concede = list(dict.fromkeys(raw.get("concede") or []))
        if any(article not in allowed for article in demand + concede):
            raise ValueError("OpenAI selected an unknown negotiation article")
        if set(demand) & set(concede):
            raise ValueError("OpenAI both demanded and conceded the same article")
        args.update({"demand": demand, "concede": concede})

    action = _validated_response_action(
        state,
        side,
        legal,
        {
            "action": selected["tool"],
            **args,
            "message": str(raw.get("message") or ""),
            "intent": _intent_for(selected["tool"], args),
        },
        model,
    )
    if not action.args.get("message"):
        raise ValueError("OpenAI returned no commander declaration")
    if not _dialogue_fits_action(action, str(action.args["message"])):
        rejected = str(action.args["message"])
        action.args["message"] = _trim_dialogue(_direct_dialogue(action, state.world.turn))
        action.dialogue_model = "guarded-fallback"
        await _trace(
            agent=side, direction="status", model=model, provider="local",
            turn=state.world.turn, stage="dialogue",
            content={
                "status": "Replaced mechanical or indirect commander dialogue.",
                "rejected": rejected,
                "replacement": action.args["message"],
            },
        )
    try:
        action.decision_confidence = max(0.0, min(1.0, float(raw.get("confidence", 0))))
    except (TypeError, ValueError):
        action.decision_confidence = 0.0
    if not action.dialogue_model:
        action.dialogue_model = model
    await _trace(
        agent=side, direction="usage", model=model, provider="openai",
        turn=state.world.turn, api="responses", stage="decision", stateless=True,
        response_id=str(_read(response, "id", "")),
        **_usage_payload(response, model),
    )
    return action


def _fallback_dialogue(action: Action) -> str:
    existing = str(action.args.get("message", "")).strip()
    if existing and _dialogue_fits_action(action, existing):
        return _trim_dialogue(existing)
    if existing:
        return _trim_dialogue(_direct_dialogue(action))
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
    commander_model = settings.model_for(side)

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
        action.args["message"] = _fallback_dialogue(action)
        action.source, action.legal, action.model = "fallback", legal, "mock"
        action.dialogue_model = "mock"
        await _trace(
            agent=side, direction="status", model="mock", provider="local",
            stage="decision", turn=state.world.turn,
            content="No OpenAI key — scripted policy selected and voiced the action.",
        )
        return action

    try:
        return await _openai_commander_action(state, side, legal, commander_model)
    except Exception as exc:  # noqa: BLE001 - never let a bad turn kill the match
        await _trace(
            agent=side, direction="error", model=commander_model,
            provider="openai", stage="decision", turn=state.world.turn,
            api="responses", content=_error_payload(exc),
        )
        action = _mock_action(state, side, legal)
        action.args["message"] = _fallback_dialogue(action)
        action.source, action.legal = "fallback", legal
        action.model = commander_model
        action.dialogue_model = "fallback"
        action.reasoning = f"[decision fallback: {type(exc).__name__}] {action.reasoning}"
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
