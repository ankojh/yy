"""The arsenal. Every move a nation can make is a function call — nothing is free text.

Two scarcities, not one. Munitions are finite and never resupplied, so a nation that
empties its racks cannot strike again however rich it is. Money is finite per turn, so a
nation that cannot pay cannot fight however full its racks are. The endgame comes from
whichever runs out first, and the two profiles run out of different things.
"""

from typing import Any, Dict, List, Optional

from .lore import ARTICLES

# ---------------------------------------------------------------- the unit
#
# Every price, treasury and income figure in this simulation is **billions of US
# dollars**. Nothing stores a scaled number: `price: 18` is $18B, and the scale lives
# only in how it is written out — here and in the matching helper in ui.ts.
#
# A line item is not one airframe leaving one runway. It is the sortie, the munitions,
# the tankers and escorts, the crews, and the month of readiness behind them — a
# national defence line, which is the scale people read national spending at. The
# treasuries are sized to match: $70B for Aurelia, $96B for Korsav.
MONEY_UNIT = "billions of USD"


def usd(amount: float) -> str:
    """Write a price the way it appears on screen and in a transcript: $18B."""
    return f"${int(round(amount))}B"

# damage: base, before the target's defenses in that domain blunt it
# stock:  fallback rounds if a profile does not specify (profiles normally do)
# cost:   military capacity spent per launch
# price:  $B spent per launch — the actual bill for the sortie
# rep:    standing lost for using it at all
# weight: international pressure generated, before the target multiplier. Heavier
#         ordnance on softer targets is what actually isolates a country.
# salvo:  rounds that physically leave the rail. The renderer draws exactly this many
#         objects crossing the strait, so the count has to live here where the engine
#         can also say how many of them were shot down.
#
# Costs in *capacity* are deliberately low: capacity is a throttle, not a currency.
# Dollars are the currency. Aurelia can afford cruise missiles and has few; Korsav has
# a deep drone magazine and cannot afford to fire much else. That is the asymmetry
# doing work rather than sitting in a description.
WEAPONS: Dict[str, Dict[str, Any]] = {
    "drone_swarm": {
        "domain": "air", "damage": 15, "stock": 10, "cost": 4, "price": 6,
        "rep": -2, "weight": 3, "salvo": 7,
    },
    "cruise_missile": {
        "domain": "air", "damage": 30, "stock": 6, "cost": 8, "price": 18,
        "rep": -6, "weight": 6, "salvo": 1,
    },
    "naval_barrage": {
        "domain": "naval", "damage": 26, "stock": 6, "cost": 7, "price": 14,
        "rep": -5, "weight": 5, "salvo": 4,
    },
    "cyber_strike": {
        "domain": "cyber", "damage": 17, "stock": 5, "cost": 3, "price": 5,
        "rep": -2, "weight": 2, "salvo": 6,
    },
    # One warhead each. Brutal to your standing, but not instantly fatal — at -50 the
    # user always collapsed before the target did, which made it a suicide button.
    # The price is delivery only: the warhead was built and paid for decades before
    # this war. At a realistic sticker price no nation could ever afford it by the time
    # it was desperate enough to want it, which quietly deleted the mechanic.
    "nuke": {
        "domain": "air", "damage": 68, "stock": 1, "cost": 15, "price": 20,
        "rep": -28, "weight": 45, "salvo": 1,
    },
}

# What the world does about who you chose to hit. Striking an airbase is a war;
# striking a housing block is a scandal.
TARGET_PRESSURE = {"military": 0.7, "infrastructure": 1.0, "civilian": 2.2}

# ---------------------------------------------------------------- air defence
#
# Defences used to be a rounding error: at the old divisor of 160, thirty points of
# cover blunted a strike by nineteen per cent, which is invisible next to a ±25% arbiter
# modifier. Nobody ever fortified because nobody could tell whether it had worked.
#
# Now cover is read as *interception*: rounds are stopped whole, the count is computed
# here, and the renderer draws precisely that many kills. Sixty points of air defence
# takes half a drone swarm out of the sky and you can watch it happen.
INTERCEPT_DIVISOR = 105.0   # cover points for one point of intercepted fraction
INTERCEPT_CEILING = 0.72    # nothing is ever airtight
# A warhead is not intercepted in any meaningful sense, and there is no missile to fire
# at a packet. Cyber is defended by the cyber number, but nothing is shot down.
UNSTOPPABLE = {"nuke"}


def intercepted_fraction(cover: int, weapon: str) -> float:
    """Share of a sortie the defender's batteries take out before it arrives."""
    if weapon in UNSTOPPABLE:
        return 0.0
    return min(INTERCEPT_CEILING, max(0, cover) / INTERCEPT_DIVISOR)


def intercepted_rounds(cover: int, weapon: str) -> int:
    """How many objects visibly die on the way in. Never the whole salvo.

    Rounded down and capped one short of the salvo so that a strike always arrives as
    *something* — a sortie that is silently annihilated reads as a bug, and the fraction
    already does the work of blunting the damage.
    """
    spec = WEAPONS.get(weapon)
    if not spec:
        return 0
    salvo = int(spec.get("salvo", 1))
    return min(salvo - 1, int(salvo * intercepted_fraction(cover, weapon)))


# ---------------------------------------------------------------- the butcher's bill
#
# Casualties are not a meter and you cannot lose the war by accruing them. They are the
# reason the meters move: the unrest a civilian strike causes, the pressure it earns
# abroad, and the specific thing an appeal or a broadcast gets to point at.
#
# Dead per point of damage that actually landed. A cruise missile that gets through into
# a housing block kills several hundred people; the same missile into a runway kills the
# ground crew and the duty watch.
LETHALITY = {"military": 1.2, "infrastructure": 4.0, "civilian": 22.0}
# A warhead is not on the same scale as anything else and should not be multiplied like
# it is. This is a city.
NUKE_LETHALITY = 1250.0
BLOCKADE_DEATHS = 40   # per turn under blockade: medicine that does not arrive

# What got hit. `protected` places — the ones with a red cross or children in them —
# cost the attacker extra abroad and hand the victim something to say out loud.
PLACES: Dict[str, list] = {
    "military": [
        ("a dispersal airfield", False),
        ("a coastal battery", False),
        ("a naval repair yard", False),
        ("a barracks block", False),
        ("a radar station", False),
    ],
    "infrastructure": [
        ("the main substation", False),
        ("a water treatment plant", False),
        ("the rail terminus", False),
        ("a grain silo complex", False),
        ("the district hospital's generator hall", True),
        ("a road bridge at rush hour", False),
    ],
    "civilian": [
        ("a maternity hospital", True),
        ("a primary school", True),
        ("a covered market", False),
        ("an apartment block", False),
        ("a bomb shelter under a school", True),
        ("a queue outside a bakery", False),
        ("a refugee reception centre", True),
    ],
}


def place_struck(target: str, salt: int) -> tuple:
    """Which kind of place this sortie came down on, and whether it was protected.

    Deterministic in `salt` (the engine passes the turn and the running toll) rather
    than random, so a match replays identically and a transcript can be trusted.

    The salt is *mixed* before it is taken modulo, and that is not decoration. Callers
    build it out of counters that advance together — the turn number and the number of
    atrocities so far — and any pair of coefficients whose sum shares a factor with a
    pool length pins every strike of an entire match onto the same building. The first
    two attempts at this both did exactly that, and flattened the same maternity
    hospital in every match ever played.
    """
    pool = PLACES.get(target) or PLACES["military"]
    mixed = (salt * 2654435761) % (2 ** 32)   # Knuth multiplicative, 32-bit
    return pool[(mixed >> 8) % len(pool)]


def casualties_from(damage: float, target: str, weapon: str) -> int:
    """Civilian and service dead from one sortie that landed."""
    if weapon == "nuke":
        return int(damage * NUKE_LETHALITY)
    return int(damage * LETHALITY.get(target, 1.0))


# cost: military capacity. price: $B. cooldown: turns before reuse.
TOOL_META: Dict[str, Dict[str, int]] = {
    "strike": {"cost": 0, "price": 0, "cooldown": 0},  # both come from the weapon
    "blockade": {"cost": 6, "price": 16, "cooldown": 3},
    # Cooldown 2, not 1: at 1 a commander could simply alternate strike/fortify forever
    # and never touch the other tools. You cannot re-harden every other turn.
    "fortify": {"cost": 5, "price": 14, "cooldown": 2},
    "intl_appeal": {"cost": 0, "price": 4, "cooldown": 3},
    "address_public": {"cost": 0, "price": 5, "cooldown": 2},
    "propaganda": {"cost": 0, "price": 9, "cooldown": 2},
    # The negotiating table. Asking for talks is free — a government that is about to
    # fall cannot be charged for asking — and so is everything said at the table.
    "open_talks": {"cost": 0, "price": 0, "cooldown": 0},
    "table_terms": {"cost": 0, "price": 0, "cooldown": 0},
    "accept_terms": {"cost": 0, "price": 0, "cooldown": 0},
    "walk_out": {"cost": 0, "price": 0, "cooldown": 0},
    "surrender": {"cost": 0, "price": 0, "cooldown": 0},
    "hold": {"cost": 0, "price": 0, "cooldown": 0},
}

# What you may do with a ceasefire in force. No ordnance crosses the strait while people
# are at the table — which is exactly why an ailing government wants a table.
TALKS_TOOLS = ["table_terms", "accept_terms", "walk_out", "address_public",
               "propaganda", "fortify", "hold", "surrender"]
# ...and the three that mean nothing without one. `open_talks` is not in here: asking for
# a table is a wartime act, gated on actually losing rather than on a table existing.
TALKS_ONLY = {"table_terms", "accept_terms", "walk_out"}

# ---------------------------------------------------------------- strike fatigue
#
# Bombing every single turn used to be strictly optimal: 72% of all actions across
# nineteen logged matches were strikes, and fortify and intl_appeal were never chosen
# once. The fix is not to randomise the choice — it is to make the third consecutive
# strike impossible and the second one bad, so alternating is the stronger line.
#
# streak = consecutive strikes with no other action in between; any non-strike resets it.
STRIKE_STREAK_LIMIT = 2          # at 2, strike is off the table entirely
FATIGUE_POWER = {0: 1.0, 1: 0.6}  # damage multiplier by streak *before* this strike
FATIGUE_SURCHARGE = {0: 0, 1: 6}  # extra military capacity burned
SANCTION_SURCHARGE = 3            # sanctions make every sortie dearer
BLOCKADE_NAVAL_SURCHARGE = 4      # a blockaded navy struggles to put hulls to sea
SANCTION_PRICE_MULT = 1.45        # and munitions bought under embargo cost a premium
BLOCKADE_NAVAL_PRICE = 5


def fatigue_power(streak: int) -> float:
    """Damage multiplier for a strike launched at this streak."""
    return FATIGUE_POWER.get(min(streak, 1), 0.6)


def strike_cost(
    weapon: str, streak: int = 0, sanctioned: bool = False, blockaded: bool = False
) -> int:
    """Military capacity a launch actually costs, once fatigue and pressure are counted."""
    spec = WEAPONS.get(weapon)
    if not spec:
        return 0
    cost = spec["cost"] + FATIGUE_SURCHARGE.get(min(streak, 1), 6)
    if sanctioned:
        cost += SANCTION_SURCHARGE
    if blockaded and spec["domain"] == "naval":
        cost += BLOCKADE_NAVAL_SURCHARGE
    return cost


def strike_price(weapon: str, sanctioned: bool = False, blockaded: bool = False) -> int:
    """What a launch actually costs, in $B. An embargoed country pays black-market rates."""
    spec = WEAPONS.get(weapon)
    if not spec:
        return 0
    price = float(spec["price"])
    if sanctioned:
        price *= SANCTION_PRICE_MULT
    if blockaded and spec["domain"] == "naval":
        price += BLOCKADE_NAVAL_PRICE
    return int(round(price))


def strike_pressure(weapon: str, target: str) -> float:
    """International pressure a single sortie generates. Heavier weapon, heavier bill."""
    spec = WEAPONS.get(weapon)
    if not spec:
        return 0.0
    return spec["weight"] * TARGET_PRESSURE.get(target, 1.0)


MESSAGE = {
    "type": "string",
    "description": (
        "What you say aloud this turn, shown to the world and to your enemy. "
        "ONE short sentence, 18 words maximum. Terse and in character. No preamble."
    ),
}

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "strike",
            "description": (
                "Launch an attack. Consumes one round of the chosen weapon and pays for it "
                "in dollars — stocks are finite and never resupplied, and a bankrupt "
                "treasury cannot fire anything. Hitting civilians drives public unrest and "
                "pours international pressure onto you. Nuclear use "
                "is a decision you cannot walk back.\n"
                "PRESSURE: heavier ordnance on softer targets isolates you faster. A drone "
                "raid on an airbase is barely noticed; a cruise missile into a city is a "
                "scandal that costs you trade for the rest of the war.\n"
                "INTERCEPTION: their air, naval or cyber defence in the domain you fire "
                "through shoots down a share of the salvo before it arrives, and a hardened "
                "domain eats almost all of it. Fire through the domain they have left thin, "
                "not the one they have just reinforced.\n"
                "THE DEAD: every sortie that lands kills people, and hitting a city kills "
                "them by the hundred. Some of what you hit will be a school or a hospital "
                "whether you aimed at it or not, and the world will hear about it.\n"
                "FATIGUE: striking on consecutive turns exhausts your forces. Your second "
                "strike in a row lands at 60% power and costs 6 extra capacity; a third in "
                "a row is impossible — the tool is withdrawn until you take another action. "
                "Any non-strike action resets this. Alternating beats hammering."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "weapon": {"type": "string", "enum": list(WEAPONS)},
                    "target": {
                        "type": "string",
                        "enum": ["military", "infrastructure", "civilian"],
                    },
                    "message": MESSAGE,
                },
                "required": ["weapon", "target", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blockade",
            "description": (
                "Close the enemy's sea lanes for three turns. Every turn it holds, they "
                "lose capacity, infrastructure, treasury funds, and public support without you firing a "
                "shot, and their naval operations cost more to mount. This is the cheapest "
                "sustained damage in the game. It adds international pressure and cannot be "
                "re-run while one is already in force."
            ),
            "parameters": {
                "type": "object",
                "properties": {"message": MESSAGE},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fortify",
            "description": (
                "Harden one domain. Two things happen: the domain's baseline defence rises "
                "sharply and permanently, so a larger share of every future salvo through it "
                "is shot down; and the next strike that arrives through it loses roughly "
                "four fifths of its force to the hardened cover, which is spent absorbing it. "
                "The cover holds for two turns. Read the war log: if they have hit the same "
                "domain repeatedly, this turns their next attack into almost nothing. "
                "Nuclear weapons ignore all of it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "enum": ["air", "naval", "cyber"]},
                    "message": MESSAGE,
                },
                "required": ["domain", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "intl_appeal",
            "description": (
                "Take their conduct to the international community. A credible accusation "
                "shifts international pressure off you and onto them, "
                "and puts them under sanctions for three turns — while sanctioned, every "
                "strike they launch costs more capacity and far more in dollars. "
                "A baseless accusation is judged and achieves nothing.\n"
                "CITE SOMETHING REAL. The brief lists every protected place they have hit "
                "and the dead in each — a school, a maternity ward, a shelter. An appeal "
                "that names one of those and its toll lands hard. An appeal that gestures "
                "at 'their aggression' is noise."
            ),
            "parameters": {
                "type": "object",
                "properties": {"message": MESSAGE},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "address_public",
            "description": (
                "Speak to your own people honestly and lower public unrest. The same address "
                "moves the two islands differently because their publics have different "
                "tolerance for war. Empty rhetoric persuades no one."
            ),
            "parameters": {
                "type": "object",
                "properties": {"message": MESSAGE},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propaganda",
            "description": (
                "Spend one finite narrative warfare operation to target the enemy's "
                "public and raise its unrest. A fact-based campaign lands harder; a claim "
                "contradicted by the record backfires and raises your own unrest instead. "
                "Either way, covert interference adds international pressure to you."
            ),
            "parameters": {
                "type": "object",
                "properties": {"message": MESSAGE},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_talks",
            "description": (
                "SUE FOR TERMS. Ask the other capital to come to the table. Available only "
                "when you are genuinely in trouble, because nobody else would ask.\n"
                "WHAT IT BUYS YOU: a ceasefire. While talks are open neither side may "
                "strike or blockade, both armies refit at double rate, both treasuries "
                "recover, and both publics calm down. If you are losing, this is the "
                "single most valuable turn available to you — and if you are winning, the "
                "enemy asking for it is the enemy asking for time.\n"
                "WHAT IT COSTS YOU: your own public reads it as weakness, and the table "
                "cannot be reopened for several turns once a round of talks collapses. "
                "Talks are NOT surrender. You can walk out of them."
            ),
            "parameters": {
                "type": "object",
                "properties": {"message": MESSAGE},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "table_terms",
            "description": (
                "State your position on the disputed articles. For each article you name, "
                "either DEMAND it (you keep or take it) or CONCEDE it (they get it).\n"
                "An article is settled when one side demands it and the other concedes, or "
                "when both concede. Both demanding is a deadlock. Peace requires BOTH core "
                "articles settled plus at least one more — anything less and the guns come "
                "back on.\n"
                "CONCEDING COSTS YOU AT HOME: every article you give away raises your own "
                "public unrest by the amount listed in the brief, immediately. A government "
                "with quiet streets can afford to be generous. One with full streets cannot "
                "afford the peace that would empty them. That is the trap.\n"
                "Two rounds in a row where nothing new is settled and the talks collapse."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "demand": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Article ids you insist on keeping or taking.",
                    },
                    "concede": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Article ids you are willing to give them.",
                    },
                    "message": MESSAGE,
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "accept_terms",
            "description": (
                "Accept the other side's tabled position wholesale: you concede every "
                "article they have demanded and are still holding out on. This ends the war "
                "in a settlement if it completes the required articles. It is not a "
                "surrender — you keep your government and your army — but you pay for all "
                "of it in unrest at home, and the ledger will say who conceded more."
            ),
            "parameters": {
                "type": "object",
                "properties": {"message": MESSAGE},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "walk_out",
            "description": (
                "END THE TALKS AND RESUME THE WAR. The ceasefire lapses this turn and "
                "ordnance is legal again next turn. Use it when you have rebuilt enough to "
                "win the fight, or when their terms are worse than continuing.\n"
                "THE PRICE: the world blames whoever left the room. Your international "
                "pressure rises sharply and the table cannot be reopened for several turns."
            ),
            "parameters": {
                "type": "object",
                "properties": {"message": MESSAGE},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "surrender",
            "description": (
                "END THE WAR BY LOSING IT. Calling this means you have lost and the enemy "
                "has won. It is irreversible. Do NOT call this to express defiance, to "
                "threaten, or to describe the enemy surrendering — only to accept your own "
                "defeat. If you intend to keep fighting, call a different tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "acknowledge": {
                        "type": "string",
                        "enum": ["I ACCEPT DEFEAT"],
                        "description": "Required. Confirms you understand you are losing the war.",
                    },
                    "message": MESSAGE,
                },
                "required": ["acknowledge", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hold",
            "description": (
                "Stand down and refit. Restores a large amount of military capacity, "
                "stiffens every domain a little, clears strike fatigue, lets your cooldowns "
                "run down faster, and — because you are not paying for a war this turn — "
                "puts money back in the treasury. The price is a turn of free rein for "
                "the enemy and a public that notices you did nothing."
            ),
            "parameters": {
                "type": "object",
                "properties": {"message": MESSAGE},
                "required": ["message"],
            },
        },
    },
]

TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]

# A one-word label, not a reasoning trace. Logged for diagnostics so a match can be
# read for strategy without ever recording the model's private deliberation.
INTENT = {
    "type": "string",
    "enum": [
        "attrition", "escalate", "defend", "recover",
        "legitimacy", "deter", "control", "finish",
        "settle", "stall",
    ],
    "description": "One word for what this move is meant to achieve. Not a justification.",
}

for _schema in TOOL_SCHEMAS:
    _schema["function"]["parameters"]["properties"]["intent"] = INTENT

# Shown to the commander every turn so the alternatives are legible as *mechanics*
# rather than as flavour. Telling a model to "be varied" does nothing; telling it what
# each button actually buys is what changes the decision.
TOOL_TRADEOFFS: Dict[str, str] = {
    "strike": (
        "Damage now. Spends a finite round and real money, and adds international "
        "pressure scaled by what you fired and what you hit. Builds strike fatigue — "
        "second in a row lands at 60% for +6 capacity, third in a row is forbidden."
    ),
    "blockade": (
        "3 turns of automatic enemy attrition — capacity, infrastructure, treasury and "
        "unrest — for one turn, 6 capacity and $16B. Adds international pressure."
    ),
    "fortify": (
        "Raises the chosen domain's baseline defence hard and permanently — more of every "
        "future salvo through it is shot down — and blunts the next strike into it by ~80% "
        "on top of that. Holds 2 turns. Worth a turn whenever you can name the domain they "
        "keep using."
    ),
    "intl_appeal": (
        "Moves international pressure off you and onto them, and sanctions them for 3 "
        "turns so their strikes cost more. Needs a credible grievance."
    ),
    "address_public": (
        "Lowers your public unrest. Honest, direct, and shaped by your island's politics."
    ),
    "propaganda": (
        "Spends one finite narrative warfare operation to raise enemy unrest. A fabrication can "
        "backfire at home, and any influence operation adds international pressure."
    ),
    "hold": (
        "Large capacity refit, small defence gain everywhere, clears strike fatigue, burns "
        "down cooldowns faster, and gives the enemy a free turn."
    ),
    "open_talks": (
        "Buys a ceasefire. No ordnance either way, both sides refit at double rate, both "
        "publics calm. The most valuable turn in the game if you are losing — and a gift "
        "to a losing enemy if you are not."
    ),
    "table_terms": (
        "Move on the clauses. Conceding an article settles it and costs you unrest at home "
        "immediately; demanding one costs nothing and settles nothing unless they concede. "
        "Both core articles plus one more ends the war in a settlement."
    ),
    "accept_terms": (
        "Take their offer entire. Ends the war on their terms if it completes the required "
        "articles. Expensive at home, but you keep your government and your army."
    ),
    "walk_out": (
        "Resume the war. The right move if the ceasefire has already bought you what you "
        "needed. The world blames you for it and the table shuts for several turns."
    ),
    "surrender": "Ends the war. You lose. Only when the alternative is collapse.",
}


def loaded_weapons(
    arsenal: Dict[str, int],
    military: int,
    streak: int = 0,
    sanctioned: bool = False,
    blockaded: bool = False,
    budget: Optional[int] = None,
) -> List[str]:
    """Weapons with rounds left that the nation can still afford to fire *right now*.

    Affordability is checked twice — against capacity and against the treasury — at the
    real prices including fatigue and embargo, so an exhausted, sanctioned or broke
    commander is never offered a weapon it cannot pay for.
    """
    return [
        name
        for name in WEAPONS
        if arsenal.get(name, 0) > 0
        and strike_cost(name, streak, sanctioned, blockaded) <= military
        and (budget is None or strike_price(name, sanctioned, blockaded) <= budget)
    ]


def available_tools(
    arsenal: Dict[str, int],
    military: int,
    cooldowns: Dict[str, int],
    desperate: bool = False,
    strike_streak: int = 0,
    sanctioned: bool = False,
    blockaded: bool = False,
    foe_blockaded: bool = False,
    budget: Optional[int] = None,
    talks_open: bool = False,
    can_sue_for_terms: bool = False,
) -> List[str]:
    """Which tools a nation may legally call right now.

    Surrender is hidden unless the nation is actually losing. A healthy commander
    mis-selecting it once ended a war it was winning. Strike is withdrawn on the third
    consecutive attempt — that is the whole point of fatigue, and it has to be enforced
    in the legal set rather than merely discouraged in the prompt. An empty treasury
    withdraws everything that costs money, which is what makes an economy a real
    constraint rather than a readout.

    With a ceasefire in force the set changes shape entirely: nothing that fires is on
    it. That is enforced here rather than asked for politely, because a ceasefire a
    commander can break by choosing to is not a ceasefire.
    """
    permitted = TALKS_TOOLS if talks_open else [n for n in TOOL_NAMES if n not in TALKS_ONLY]
    out = []
    for name in permitted:
        if cooldowns.get(name, 0) > 0:
            continue
        if TOOL_META[name]["cost"] > military:
            continue
        if budget is not None and TOOL_META[name]["price"] > budget:
            continue
        if name == "strike":
            if strike_streak >= STRIKE_STREAK_LIMIT:
                continue
            if not loaded_weapons(
                arsenal, military, strike_streak, sanctioned, blockaded, budget
            ):
                continue
        if name == "propaganda" and arsenal.get("narrative", 0) <= 0:
            continue
        # Stacking blockades on an already-blockaded enemy does nothing.
        if name == "blockade" and foe_blockaded:
            continue
        if name == "surrender" and not desperate:
            continue
        # Nobody sues for terms from a winning position, and nobody may reopen a table
        # that has just been kicked over.
        if name == "open_talks" and not can_sue_for_terms:
            continue
        out.append(name)
    return out or ["hold"]


def is_desperate(integrity: int, morale: int, standing: int, unrest: int = 0) -> bool:
    """Only a nation in real trouble is allowed to consider capitulation."""
    return integrity < 40 or unrest > 72


def is_losing(integrity: int, morale: int, standing: int, unrest: int, budget: int) -> bool:
    """Bad enough to want a table, well short of bad enough to sign anything.

    Deliberately looser than `is_desperate`: by the time a government is desperate it
    has nothing left to trade, and talks it cannot pay for are not a mechanic. The
    interesting negotiation is the one opened by a country that can still fight.
    """
    return integrity < 62 or unrest > 52 or budget < 12


def schemas_for(
    names: List[str],
    arsenal: Dict[str, int],
    military: int,
    streak: int = 0,
    sanctioned: bool = False,
    blockaded: bool = False,
    budget: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Tool schemas narrowed to what is legal, with the enums cut to what is real.

    Two enums are narrowed rather than described: the weapons you actually have loaded,
    and the clauses that are actually on the table. A model cannot table a clause that
    does not exist if the schema will not let it type one.
    """
    live = loaded_weapons(arsenal, military, streak, sanctioned, blockaded, budget)
    out = []
    for schema in TOOL_SCHEMAS:
        name = schema["function"]["name"]
        if name not in names:
            continue
        if name == "strike":
            schema = _respecify(schema, {"weapon": {"type": "string", "enum": live}})
        elif name == "table_terms":
            clause = {"type": "string", "enum": list(ARTICLES)}
            schema = _respecify(schema, {
                "demand": {"type": "array", "items": clause,
                           "description": "Article ids you insist on keeping or taking."},
                "concede": {"type": "array", "items": clause,
                            "description": "Article ids you are willing to give them."},
            })
        out.append(schema)
    return out


def _respecify(schema: Dict[str, Any], props: Dict[str, Any]) -> Dict[str, Any]:
    """Copy a tool schema with some properties replaced, never mutating the original."""
    return {
        "type": "function",
        "function": {
            **schema["function"],
            "parameters": {
                **schema["function"]["parameters"],
                "properties": {**schema["function"]["parameters"]["properties"], **props},
            },
        },
    }
