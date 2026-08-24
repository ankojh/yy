from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .lore import ACCOUNTS
from .nations import PROFILES

Side = Literal["west", "east"]
Domain = Literal["air", "naval", "cyber"]

OPPONENT: Dict[str, str] = {"west": "east", "east": "west"}


def clamp(value: float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


class Traits(BaseModel):
    """How the world and the home front treat this particular country.

    Every one of these is a multiplier the engine reads. Two nations running the same
    strategy get different bills for it, which is the whole point of asymmetry.
    """

    intl_sensitivity: float = 1.0   # how fast international pressure accrues against you
    unrest_sensitivity: float = 1.0  # how quickly your public turns on the war
    propaganda_reach: float = 1.0    # how much a dollar of propaganda actually buys at home
    trade_exposure: float = 1.0      # how much isolation costs your economy
    industry: float = 1.0            # how quickly capacity and output rebuild


class Effects(BaseModel):
    """Persistent consequences that outlive the turn that created them.

    Without these the non-strike tools were all one-turn nudges, and a one-turn nudge
    never beats a strike. These are what make the other tools worth a turn.
    """

    # fortify: turns of hardened cover remaining, per domain. The next strike that
    # arrives through a covered domain is mostly absorbed, and spends the cover.
    shield: Dict[str, int] = Field(default_factory=dict)
    # address_public: morale damage this nation can soak before the public feels it.
    morale_buffer: int = 0
    # blockade: turns remaining under an enemy blockade. Bleeds capacity every upkeep,
    # strangles trade, and makes naval operations more expensive to mount.
    blockaded: int = 0
    # intl_appeal: turns remaining of sanctions. Raises the cost of every strike and
    # doubles the standing you burn by continuing to escalate.
    sanctioned: int = 0
    # propaganda: turns of an active information campaign. While it runs, unrest grows
    # far slower — and the international read of your conduct gets correspondingly worse.
    spin: int = 0
    # Consecutive upkeeps the treasury could not cover its bills. Deficits are survivable;
    # a run of them is not.
    deficit_turns: int = 0
    # Sub-unit remainders for the slow meters. The meters are integers, so any drift of
    # less than half a point per turn used to round to nothing and vanish for the whole
    # war — which silently made a heavily propagandised public completely immune to war
    # weariness rather than merely resistant to it.
    carry: Dict[str, float] = Field(default_factory=dict)

    def summary(self) -> Dict[str, Any]:
        live = {d: t for d, t in self.shield.items() if t > 0}
        return {
            "hardened_domains": live,
            "morale_buffer": self.morale_buffer,
            "under_blockade_for_turns": self.blockaded,
            "under_sanctions_for_turns": self.sanctioned,
            "propaganda_campaign_turns_left": self.spin,
            "consecutive_deficit_turns": self.deficit_turns,
        }


class Nation(BaseModel):
    side: Side
    name: str
    blurb: str = ""
    creed: str = ""         # the founding story this country argues from
    # Legacy fields remain parseable so old recordings still load. New matches expose
    # and resolve only infrastructure, military, treasury, unrest, and pressure.
    morale: int = 60
    military: int = 70      # capacity to act; spent by every offensive move
    # How much of the opponent's operational state this cabinet can see. Low values
    # yield broad intelligence bands; sustained investment eventually unlocks the
    # opponent's exact current state in the commander brief.
    intelligence: int = 40
    standing: int = 70
    integrity: int = 100    # infrastructure and territory still intact
    gdp: int = 70
    gdp_base: int = 70
    budget: int = 80        # treasury, in $B. Weapons and operations are bought out of it.
    # The two pressures. International is what the world does to you; public is what
    # your own people do to you. Both are per-nation and both are earned separately.
    intl_pressure: int = 0  # 0..100. At 100 you are a pariah and your standing collapses.
    unrest: int = 10        # 0..100. At 100 the government falls and the war is over.
    propaganda: int = 30
    # The bill nobody's meter shows. Cumulative civilian dead on this nation's soil —
    # not a way to lose, but the reason its streets fill and the reason the other side
    # has something to take to the council.
    casualties: int = 0
    defenses: Dict[str, int] = Field(
        default_factory=lambda: {"air": 20, "naval": 20, "cyber": 20}
    )
    # Turns remaining before each tool can be used again. Scarcity forces variety.
    cooldowns: Dict[str, int] = Field(default_factory=dict)
    # Rounds left per weapon. Never resupplied — running dry is how wars end.
    arsenal: Dict[str, int] = Field(default_factory=dict)
    # Consecutive strikes with no other move in between. Reset by any non-strike action.
    strike_streak: int = 0
    traits: Traits = Field(default_factory=Traits)
    effects: Effects = Field(default_factory=Effects)
    # Landform hints for the map. The backend never draws anything; it only guarantees
    # each nation looks like itself from match to match.
    terrain: Dict[str, Any] = Field(default_factory=dict)

    def spin_strength(self) -> float:
        """How much the state's account of the war is currently displacing reality.

        Deliberately weighted towards the *active* campaign rather than the standing
        apparatus. When capability alone carried it, Korsav's peacetime broadcasters
        made its public so hard to move that it never once needed to run a campaign —
        the tool existed and was never worth a turn. The apparatus is what makes a
        campaign land; the campaign is what does the work.
        """
        reach = self.traits.propaganda_reach
        passive = (self.propaganda / 100) * reach * 0.45
        return passive + (0.55 * reach if self.effects.spin > 0 else 0.0)

    def coarse(self) -> Dict[str, Any]:
        """What the *other* side is allowed to see. Hidden information is the whole game."""

        def band(v: float) -> str:
            if v >= 75:
                return "strong"
            if v >= 50:
                return "holding"
            if v >= 25:
                return "strained"
            return "critical"

        def rising(v: float) -> str:
            if v >= 75:
                return "boiling"
            if v >= 50:
                return "restive"
            if v >= 25:
                return "grumbling"
            return "quiet"

        return {
            "name": self.name,
            "military": band(self.military),
            "infrastructure": band(self.integrity),
            "public_mood": rising(self.unrest),
            # The one number that is never hidden. Bodies are counted by everybody.
            "civilian_dead": self.casualties,
            # Sanctions, condemnation and embargo are matters of public record.
            "international_pressure": self.intl_pressure,
            # Public facts: they announced the fortification, and you are the one who
            # imposed the blockade and the sanctions. The morale buffer stays hidden.
            "visibly_hardened": sorted(d for d, t in self.effects.shield.items() if t > 0),
            "under_blockade": self.effects.blockaded > 0,
            "under_sanctions": self.effects.sanctioned > 0,
            "strike_streak": self.strike_streak,
        }


class TurnRecord(BaseModel):
    """One declared action and what it did. This is the agents' memory of the war."""

    turn: int
    side: Side
    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    effective: bool = True
    deltas: List[Dict[str, Any]] = Field(default_factory=list)


class Atrocity(BaseModel):
    """A named place that was hit, and what it cost. The evidence base for an appeal.

    Recorded whoever caused it and whatever they meant by it. A commander citing one of
    these in a council appeal is citing something that actually happened in this match;
    a commander inventing one is citing something the arbiter can check.
    """

    turn: int
    victim: Side       # whose country it was in
    attacker: Side
    place: str         # "a maternity hospital in Kestrel Bay"
    dead: int
    weapon: str
    protected: bool    # a school, a hospital, a shelter — not merely a civilian address


# What a side is asking for on one clause. "silent" is the default: an article nobody
# has spoken about is not agreed, it is simply not on the table yet.
Stance = Literal["demand", "concede", "silent"]


class Talks(BaseModel):
    """An open negotiation. The buffer, and the thing that stalls in it.

    While talks are open there is a ceasefire: neither side may strike or blockade, and
    both rebuild. That is the exploit and it is deliberate — a losing government can buy
    itself three turns of refit by asking for peace it does not intend to sign, and the
    other side has to decide whether to keep talking to a liar who is reloading.
    """

    open: bool = False
    round: int = 0
    opened_by: Optional[Side] = None
    # article id -> {"west": Stance, "east": Stance}
    positions: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    settled: List[str] = Field(default_factory=list)
    # Consecutive rounds in which nothing new was agreed. Two of these and the talks
    # are over — this is the "stall" and it is the common outcome, not the rare one.
    deadlock: int = 0
    transcript: List[str] = Field(default_factory=list)

    def stance(self, article: str, side: str) -> str:
        return self.positions.get(article, {}).get(side, "silent")

    def table(self, article: str, side: str, stance: str) -> None:
        self.positions.setdefault(article, {"west": "silent", "east": "silent"})[side] = stance

    def summary(self) -> Dict[str, Any]:
        return {
            "round": self.round,
            "opened_by": self.opened_by,
            "settled": list(self.settled),
            "unsettled": [a for a in self.positions if a not in self.settled],
            "rounds_without_agreement": self.deadlock,
        }


class CouncilRequest(BaseModel):
    """One island asking the neutral council for a specific, priced package."""

    id: str
    side: Side
    kind: str
    label: str
    cost: int
    text: str
    turn: int


class World(BaseModel):
    turn: int = 0
    tension: int = 10        # how hot the conflict is
    phase: Literal["briefing", "conflict", "over"] = "briefing"
    loser: Optional[Side] = None
    grievances: List[str] = Field(default_factory=list)
    history: List[TurnRecord] = Field(default_factory=list)
    atrocities: List[Atrocity] = Field(default_factory=list)
    talks: Talks = Field(default_factory=Talks)
    # Turns before anyone may ask for talks again. Set when a round of them collapses,
    # so a side cannot open negotiations every single turn purely to stop the bombing.
    talks_cooldown: int = 0
    talks_held: int = 0      # how many rounds of negotiation this war has already burned
    outcome: Optional[str] = None
    # The player chairs a neutral international council. It can back either or both
    # belligerents, but only through finite grants; it never receives a combat turn.
    council_budget: int = 72  # billions of US dollars
    council_history: List[Dict[str, Any]] = Field(default_factory=list)
    council_request: Optional[CouncilRequest] = None


def _nation(side: str) -> Nation:
    """Build a belligerent from its profile. Nations are not interchangeable."""
    profile = PROFILES[side]
    return Nation(
        side=side,  # type: ignore[arg-type]
        name=profile["name"],
        blurb=profile["blurb"],
        creed=ACCOUNTS[side]["creed"],
        morale=profile["morale"],
        military=profile["military"],
        intelligence=profile.get("intelligence", 40),
        standing=profile["standing"],
        integrity=profile["integrity"],
        gdp=profile["gdp"],
        gdp_base=profile["gdp"],
        budget=profile["budget"],
        propaganda=profile["propaganda"],
        unrest=profile["unrest"],
        defenses=dict(profile["defenses"]),
        arsenal=dict(profile["arsenal"]),
        traits=Traits(**profile["traits"]),
        terrain=dict(profile["terrain"]),
    )


class GameState(BaseModel):
    world: World = Field(default_factory=World)
    west: Nation = Field(default_factory=lambda: _nation("west"))
    east: Nation = Field(default_factory=lambda: _nation("east"))

    def nation(self, side: str) -> Nation:
        return self.west if side == "west" else self.east

    def foe(self, side: str) -> Nation:
        return self.nation(OPPONENT[side])


class Action(BaseModel):
    side: Side
    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""
    # Diagnostics: where this decision actually came from, so a transcript can never
    # be misread as model behaviour when it was really the scripted fallback.
    source: Literal["live", "mock", "fallback"] = "mock"
    model: str = ""
    # A short structured label, never free-form reasoning traces.
    intent: str = ""
    legal: List[str] = Field(default_factory=list)

    def signature(self) -> str:
        """What makes two turns 'the same move' for repetition purposes."""
        detail = "/".join(
            str(self.args[k]) for k in ("weapon", "target", "domain") if k in self.args
        )
        return f"{self.tool}:{detail}" if detail else self.tool


class Ruling(BaseModel):
    """The arbiter's judgment on one declared action."""

    side: Side
    effective: bool = True
    modifier: int = 0   # -2..+2, scales magnitude
    reason: str = ""
    # Which findings produced the modifier. Logged so calibration is auditable.
    flags: Dict[str, bool] = Field(default_factory=dict)


class Event(BaseModel):
    """One entry in the stream the UI renders. The UI is a pure function of this log."""

    type: str
    turn: int = 0
    payload: Dict[str, Any] = Field(default_factory=dict)


def initial_state() -> GameState:
    return GameState()
