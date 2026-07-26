"""Match orchestration: ignition, the paced turn loop, and the event stream the UI renders."""

import asyncio
from typing import Any, Callable, Dict, List, Optional

from . import agents, engine
from .config import BALANCE_VERSION, settings
from .lore import ACCOUNTS, ARTICLES, PARTITION
from .matchlog import MatchLog
from .state import Action, Event, GameState, TurnRecord, clamp, initial_state

# The user does not command either side. They decide what the war is *about*.
#
# Each card is a dossier, not a headline. The card face carries the title alone; the
# `text`, the `timeline` and the two capitals' `positions` are what you get when you open
# it. The timeline is the point — none of these incidents comes out of nowhere, and a
# reader who can see the fortnight leading up to one can tell why the war that follows
# looks the way it does.
IGNITIONS: Dict[str, Dict[str, Any]] = {
    "trawler": {
        "label": "Trawler sunk",
        "text": "An Aurelian fishing trawler is sunk in disputed water. Korsav says it strayed; Aurelia says it was murdered.",
        "timeline": [
            {"when": "March", "what": "Korsav declares a live-fire exercise box straddling the Kestrel Line and renews it monthly."},
            {"when": "17 days ago", "what": "Aurelia's fisheries ministry tells its fleet the box has no standing in law and to work the grounds as normal."},
            {"when": "4 days ago", "what": "The trawler Merrow Bell is warned off twice by a Korsavi patrol and does not turn."},
            {"when": "yesterday", "what": "The Merrow Bell goes down in ninety seconds. Nine crew. Two bodies recovered."},
        ],
        "positions": {
            "west": "A civilian boat, in water that has been Aurelian since the commission, sunk without warning.",
            "east": "A boat that ignored two warnings inside a declared exercise area, on a line nobody has ever signed.",
        },
        "tension": 18,
        "effects": {
            "west": {"morale": 8, "standing": 4, "unrest": 6},
            "east": {"standing": -6, "intl_pressure": 8},
        },
    },
    "reef": {
        "label": "Gas found under the reef",
        "text": "A survey finds gas under Bellow Reef. Both nations have claimed the reef on paper for forty years and neither has ever needed to mean it.",
        "timeline": [
            {"when": "40 years", "what": "Both capitals lodge paper claims to Bellow Reef with a commission that never sits. Nothing is under it worth arguing about."},
            {"when": "8 months ago", "what": "A third-country survey ship runs seismic lines across the reef under a licence issued by neither."},
            {"when": "3 weeks ago", "what": "The data leaks. Somewhere between nine and fourteen years of Korsavi consumption, on the wrong side of an unsigned line."},
            {"when": "last week", "what": "Both navies file notices to mariners for the same fortnight, in the same square of water."},
        ],
        "positions": {
            "west": "The reef sits inside the Kestrel Line. There is nothing to discuss and a great deal to drill.",
            "east": "The line is not law. A field this size is not a windfall for the north, it is the ledger being paid.",
        },
        "tension": 12,
        # The only card that makes both economies richer for a moment. Everyone can
        # afford a war they have just found the money for.
        "effects": {"west": {"morale": 5, "gdp": 4}, "east": {"morale": 5, "gdp": 6}},
    },
    "envoy": {
        "label": "Envoy assassinated",
        "text": "Korsav's envoy is shot dead on an Aurelian quay. The gunman is dead. Nobody can say who sent him.",
        "timeline": [
            {"when": "6 weeks ago", "what": "Korsav sends an envoy north for the first time in nine years, to talk about the cable tariff and nothing else."},
            {"when": "3 weeks ago", "what": "Two rounds of talks produce a metering formula. Aurelian dockworkers picket the building daily."},
            {"when": "5 days ago", "what": "An Anvil deportees' association names the envoy in a pamphlet as the officer who cleared Ostrig in his twenties."},
            {"when": "yesterday", "what": "Three shots on the quay at Vaelport. The gunman is killed by security within the minute and carries no papers."},
        ],
        "positions": {
            "west": "A murder on Aurelian soil, by an Aurelian citizen, which Aurelia is investigating in the open.",
            "east": "An envoy invited north under guarantee and shot in front of the men who guaranteed him.",
        },
        "tension": 30,
        "effects": {
            "east": {"morale": 14, "standing": 6, "unrest": 10},
            "west": {"standing": -10, "intl_pressure": 14},
        },
    },
    "leak": {
        "label": "War plans leaked",
        "text": "A cache of Aurelian staff documents surfaces online, detailing an invasion rehearsal codenamed COLD HARVEST.",
        "timeline": [
            {"when": "2 years ago", "what": "Aurelia's staff college begins an annual contingency series. Every general staff runs them; almost none of them leak."},
            {"when": "5 months ago", "what": "COLD HARVEST is rehearsed on maps: a landing south of Duren, the cable seized in the first six hours."},
            {"when": "9 days ago", "what": "Eleven thousand pages appear on a mirror in a third country. The metadata is intact and unfaked."},
            {"when": "6 days ago", "what": "Aurelia confirms the documents are genuine and calls them a planning exercise. Korsav mobilises its reserve."},
        ],
        "positions": {
            "west": "Every staff plans for everything. A contingency is not an intention, and publishing it is the crime here.",
            "east": "They wrote down the hour they would take the cable. We are not required to wait for the second draft.",
        },
        "tension": 20,
        "effects": {
            "west": {"standing": -14, "intl_pressure": 16, "unrest": 8},
            "east": {"morale": 10},
        },
    },
    "blackout": {
        "label": "Grid blackout",
        "text": "Korsav's eastern grid fails for nine hours. The intrusion is traced, unconvincingly, to an Aurelian address.",
        "timeline": [
            {"when": "14 months ago", "what": "Korsav routes its eastern grid onto Union-era control hardware to cut its dependence on Aurelian metering."},
            {"when": "2 months ago", "what": "Aurelian security researchers publish the hardware's authentication flaw. Korsav is given ninety days' notice."},
            {"when": "11 days ago", "what": "Nine hours of darkness from Ostrig to the coast. Two hospitals lose their generators. Forty-one die."},
            {"when": "8 days ago", "what": "The intrusion is traced to a leased address in Vaelport. It is also traced, less loudly, to two others abroad."},
        ],
        "positions": {
            "west": "An address is not an attribution, and the flaw was published to be fixed, not used.",
            "east": "Forty-one dead in the dark, in a system they told us was broken and then broke.",
        },
        "tension": 22,
        "effects": {
            "east": {"morale": 10, "standing": 5, "gdp": -5, "unrest": 7},
            "west": {"standing": -8, "intl_pressure": 9},
        },
    },
    "airspace": {
        "label": "Airspace violation",
        "text": "Korsavi jets cross the median line for eleven minutes and turn back without a word.",
        "timeline": [
            {"when": "since partition", "what": "Both air forces observe a median line neither has ever put in a treaty."},
            {"when": "7 months ago", "what": "Korsav stops answering the strait's shared air channel, calling it an Aurelian instrument."},
            {"when": "3 weeks ago", "what": "Crossings go from four a year to nine in a month, each a little deeper and a little longer."},
            {"when": "this morning", "what": "Four aircraft, eleven minutes, forty kilometres inside. Aurelian interceptors scramble and are not engaged."},
        ],
        "positions": {
            "west": "Eleven minutes is a rehearsal, not a navigation error, and the next one will be met.",
            "east": "There is no line in the air because there is no line in the water. We flew through our own sky.",
        },
        "tension": 14,
        "effects": {
            "west": {"morale": 6, "unrest": 5},
            "east": {"standing": -4, "intl_pressure": 5},
        },
    },
    "cable": {
        "label": "The cable goes dark",
        "text": "The Meridian Cable is severed forty kilometres out. Both grids brown out inside the hour, and neither capital can prove whose anchor did it.",
        "timeline": [
            {"when": "44 years", "what": "The interconnector and the gas line beside it run under the strait, owned cleanly by neither country and used daily by both."},
            {"when": "5 months ago", "what": "Aurelia raises the metering tariff again. Korsav's ministry calls the cable 'a rope around our throat with a meter on it'."},
            {"when": "6 weeks ago", "what": "A Korsavi survey vessel spends nine days working the cable corridor, on a permit for something else."},
            {"when": "0400 today", "what": "Both grids brown out. The break is forty kilometres out, in water either navy could reach, and the repair ship is Aurelian."},
        ],
        "positions": {
            "west": "They cut it, and they will be back on their knees asking us to mend it within a fortnight.",
            "east": "Their meter, their repair ship, their leverage. Ask who profits from a cable only they can fix.",
        },
        "tension": 19,
        # The one card that hurts both economies at once. Nobody starts this war rich.
        "effects": {
            "west": {"gdp": -8, "morale": 6, "unrest": 7},
            "east": {"gdp": -10, "morale": 8, "unrest": 6},
        },
    },
    "rig": {
        "label": "A rig on the reef",
        "text": "A drilling platform is towed onto Bellow Reef under Aurelian flag, with two frigates alongside it.",
        "timeline": [
            {"when": "3 months ago", "what": "The Bellow Reef survey data is public. Aurelia's cabinet reads the Kestrel Line as settling ownership and licenses the block."},
            {"when": "6 weeks ago", "what": "Korsav files an objection with a commission that has not convened in sixty-one years, and moves a submarine."},
            {"when": "9 days ago", "what": "The platform Aster Deep clears Vaelport under tow, escorted, on a route that never leaves the disputed square."},
            {"when": "this morning", "what": "It is on station and jacking down. Korsavi patrol boats are holding at four hundred metres."},
        ],
        "positions": {
            "west": "Our water, our licence, our steel. We will not ask permission to drill inside our own line.",
            "east": "They are stealing it in daylight and calling the theft a permit, exactly as they did with the shelf.",
        },
        "tension": 24,
        # Aurelia is materially better off for having done it, and pays for it abroad.
        "effects": {
            "west": {"gdp": 7, "morale": 7, "standing": -9, "intl_pressure": 11},
            "east": {"morale": 11, "unrest": 9},
        },
    },
    "memorial": {
        "label": "The twentieth Halcyon",
        "text": "On the twentieth anniversary of the ferry, an Aurelian minister repeats the verdict on the record and declines to apologise.",
        "timeline": [
            {"when": "19 years ago", "what": "An Aurelian cutter fires on the ferry Halcyon Seven off Bellow Reef. Eighty-four dead, thirty-one of them children."},
            {"when": "18 years ago", "what": "An Aurelian court finds the captain acted within a reasonable apprehension of threat. He retires on a full pension."},
            {"when": "every year since", "what": "Korsav requests an apology. Aurelia acknowledges the loss of life and declines to reopen a closed matter."},
            {"when": "yesterday", "what": "Asked at the twentieth memorial, the minister says the finding stands and that the country will not be lectured about the sea. The clip runs on every Korsavi channel by nightfall."},
        ],
        "positions": {
            "west": "A court heard it in public and published its reasoning. That is more than any Korsavi court has ever done.",
            "east": "Twenty years and eighty-four graves, and they cannot spare the one word that costs them nothing.",
        },
        "tension": 21,
        # No economics at all: this one is entirely about the two publics.
        "effects": {
            "east": {"morale": 15, "standing": 7, "unrest": 12},
            "west": {"standing": -11, "intl_pressure": 10, "unrest": 5},
        },
    },
    "registry": {
        "label": "Assets frozen",
        "text": "Aurelia freezes every Korsavi holding in its shipping registry and its banks, citing the reef objection as an unlawful threat.",
        "timeline": [
            {"when": "61 years", "what": "The Union's shipping registry stayed in the north at partition. Half the southern merchant fleet has been flagged through Vaelport ever since, because there was nowhere else."},
            {"when": "4 months ago", "what": "Korsav's new southern bloc opens a registry of its own. Reflagging is slow and most hulls have not moved."},
            {"when": "2 weeks ago", "what": "Aurelia's treasury circulates a note on 'exposure to hostile state entities'. Nobody outside the building sees it."},
            {"when": "overnight", "what": "Ninety-one Korsavi-owned hulls are frozen at their moorings and the accounts behind them are locked. Crews are told to wait."},
        ],
        "positions": {
            "west": "Lawful measures against a state that has threatened a licensed civilian platform with submarines.",
            "east": "They kept our registry for sixty-one years and have now robbed us with it. The ledger is not getting shorter.",
        },
        "tension": 17,
        # Money, aimed at the side with none. Korsav starts poorer and angrier.
        "effects": {
            "west": {"standing": -7, "intl_pressure": 9},
            "east": {"budget": -22, "gdp": -6, "morale": 12, "unrest": 8},
        },
    },
    "embargo": {
        "label": "Embargo declared",
        "text": "Korsav's trading bloc closes its ports to Aurelian hulls overnight, citing 'safety'. Nine per cent of Aurelia's exports stop moving that morning.",
        "timeline": [
            {"when": "18 months ago", "what": "Aurelia raises the Meridian Cable metering tariff for the third time in four years."},
            {"when": "6 months ago", "what": "Korsav signs a southern trading bloc into existence with four smaller states and its own registry."},
            {"when": "10 days ago", "what": "The bloc adopts a 'hull safety' standard that only Aurelian-registry ships fail."},
            {"when": "overnight", "what": "Every bloc port shuts to Aurelian hulls. Nine per cent of Aurelia's exports stop moving before breakfast."},
        ],
        "positions": {
            "west": "A tariff is a bill for a service. This is not a safety standard, it is a blockade with paperwork.",
            "east": "They have metered our own gas back to us for forty years. They can be told which ports are open.",
        },
        "tension": 16,
        # An economic casus belli, aimed squarely at the side that has an economy.
        "effects": {
            "west": {"gdp": -12, "morale": 9, "unrest": 12},
            "east": {"standing": -5, "intl_pressure": 7},
        },
    },
}


class Game:
    def __init__(
        self,
        emit: Optional[Callable[[Event], Any]] = None,
        seed: Optional[int] = None,
        write_log: bool = True,
    ) -> None:
        self.state: GameState = initial_state()
        self.log: List[Event] = []
        self.seed = seed
        # Batch simulations play hundreds of matches; writing a transcript for each
        # just litters the log directory with files nobody will read.
        self.write_log = write_log
        if seed is not None:
            agents.seed(seed)
        self._emit = emit
        self._file: Optional[MatchLog] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ events

    async def emit(self, type_: str, **payload) -> None:
        ev = engine.event(type_, self.state, **payload)
        self.log.append(ev)
        if self._file:
            self._file.write(ev)
        if self._emit:
            await self._emit(ev)

    async def emit_state(self) -> None:
        await self.emit("state", state=self.state.model_dump())

    async def _beat(self, mult: float = 1.0) -> None:
        """Deliberate pause, in multiples of BEAT so setting BEAT=0 truly disables pacing."""
        await asyncio.sleep(settings.beat * mult)

    async def _dwell(self, share: float) -> None:
        """A slice of one action's time on stage. The whole of REVEAL is one move."""
        await asyncio.sleep(settings.reveal * share)

    # ------------------------------------------------------------------ setup

    async def reset(self) -> None:
        if self._file:
            self._file.close()
            self._file = None
        self.state = initial_state()
        self.log = []
        await self.emit(
            "reset",
            ignitions=[
                {
                    "id": key,
                    "label": card["label"],
                    "text": card["text"],
                    "timeline": card["timeline"],
                    "positions": card["positions"],
                }
                for key, card in IGNITIONS.items()
            ],
            mock=settings.use_mock,
            max_turns=settings.max_turns,
            balance_version=BALANCE_VERSION,
            # Who is sitting in which chair. Never hidden: a match between two different
            # models is only interesting if you can see which was which.
            panel=settings.panel,
            # The sixty-one-year-old quarrel, for the briefing.
            partition=PARTITION,
            accounts=ACCOUNTS,
            articles={k: {"title": v["title"], "dispute": v["dispute"], "core": v["core"]}
                      for k, v in ARTICLES.items()},
            # The UI holds every tooltip and bubble for exactly as long as the beat it
            # belongs to, so pacing is configured in one place rather than two.
            reveal_ms=int(settings.reveal * 1000),
        )
        await self.emit_state()

    async def ignite(self, ids: List[str], custom: str = "") -> None:
        """Light the fuse. Multiple cards stack, and a stacked war starts much hotter."""
        world = self.state.world
        chosen = []
        for key in ids:
            card = IGNITIONS.get(key)
            if not card:
                continue
            chosen.append(card["label"])
            world.tension = clamp(world.tension + card["tension"])
            world.grievances.append(card["text"])
            for side, fields in card["effects"].items():
                nation = self.state.nation(side)
                for field, amount in fields.items():
                    # The treasury is money, not a meter, and does not stop at a hundred.
                    # Korsav opens on $96B, so a card that moved its budget at all was
                    # one point away from being silently capped by the meter clamp.
                    high = engine.BUDGET_CEILING if field == "budget" else 100
                    setattr(nation, field, clamp(getattr(nation, field) + amount, 0, high))
                # Output is measured against what the country was worth before the war,
                # so a card that wrecks or enriches an economy has to move the ceiling
                # with it — otherwise a bombed-out economy climbs straight back up.
                if "gdp" in fields:
                    nation.gdp_base = nation.gdp

        if custom.strip():
            chosen.append("Custom")
            world.grievances.append(custom.strip()[:500])
            world.tension = clamp(world.tension + 15)

        if not chosen:
            return

        world.phase = "conflict"
        if self.write_log:
            # Stamp the rules the transcript was produced under. A log read against the
            # wrong balance is worse than no log.
            self._file = MatchLog(
                settings.log_dir,
                meta={
                    "balance_version": BALANCE_VERSION,
                    # Three chairs, three models. A transcript that does not say which
                    # model played which country cannot be read for anything at all.
                    "west_model": settings.panel["west"],
                    "east_model": settings.panel["east"],
                    "arbiter_model": settings.panel["arbiter"],
                    "mock": settings.use_mock,
                    "max_turns": settings.max_turns,
                    "seed": self.seed,
                },
            )
        await self.emit("ignition", cards=chosen, grievances=world.grievances)
        await self.emit_state()

    async def configure(self, setup: Dict[str, Any]) -> None:
        """Set both sides' opening position. Only legal before the first shot."""
        if self.state.world.phase != "briefing":
            return
        for side in ("west", "east"):
            block = setup.get(side)
            if not isinstance(block, dict):
                continue
            nation = self.state.nation(side)
            for field in ("integrity", "morale", "military", "standing",
                          "gdp", "propaganda", "unrest"):
                if field in block:
                    try:
                        setattr(nation, field, clamp(float(block[field])))
                    except (TypeError, ValueError):
                        pass
            # Output is set relative to a pre-war baseline, so an economy you dialled
            # down starts down and can never climb back past where you put it.
            if "gdp" in block:
                nation.gdp_base = nation.gdp
            if "budget" in block:
                try:
                    nation.budget = int(max(0, min(400, float(block["budget"]))))
                except (TypeError, ValueError):
                    pass
            arsenal = block.get("arsenal")
            if isinstance(arsenal, dict):
                for weapon in nation.arsenal:
                    if weapon in arsenal:
                        try:
                            # Nukes are capped harder — a dozen warheads is not a game.
                            ceiling = 5 if weapon == "nuke" else 30
                            nation.arsenal[weapon] = int(
                                max(0, min(ceiling, float(arsenal[weapon])))
                            )
                        except (TypeError, ValueError):
                            pass
        await self.emit_state()

    async def inject(self, text: str) -> None:
        """Drop something new on the table mid-war. Both commanders see it next turn."""
        if not text.strip():
            return
        self.state.world.grievances.append(text.strip()[:500])
        self.state.world.tension = clamp(self.state.world.tension + 8)
        await self.emit("injection", text=text.strip()[:500])
        await self.emit_state()

    # ------------------------------------------------------------------ turn loop

    async def step(self) -> None:
        async with self._lock:
            world = self.state.world
            if world.phase != "conflict":
                return

            world.turn += 1
            await self.emit("turn_started", turn=world.turn)
            await self._beat(0.45)

            # Both commanders decide simultaneously — neither sees the other's move.
            west, east = await asyncio.gather(
                agents.decide(self.state, "west"), agents.decide(self.state, "east")
            )
            actions: List[Action] = [west, east]

            # Both were judged together — they decided simultaneously — but they are
            # revealed one at a time: speak, let it land, resolve, then the other answers.
            raw = await agents.arbitrate(self.state, actions)
            rulings = agents.parse_rulings(raw, actions, self.state)
            reaction = agents.parse_world_reaction(raw)

            # A nation that quits does so before the other side's ordnance leaves the
            # rail. Resolving in list order let a strike land on a country that had
            # already capitulated, purely because "west" sorts first.
            pairs = sorted(zip(actions, rulings), key=lambda p: p[0].tool != "surrender")
            # ...unless both quit at once, in which case both are heard out.
            both_quit = all(engine.valid_surrender(a, r) for a, r in pairs)

            for action, ruling in pairs:
                await self.emit(
                    "decision",
                    side=action.side,
                    source=action.source,
                    model=action.model,
                    intent=action.intent,
                    legal=action.legal,
                    strike_streak=self.state.nation(action.side).strike_streak,
                    effects=self.state.nation(action.side).effects.model_dump(),
                )
                await self.emit(
                    "message",
                    side=action.side,
                    name=self.state.nation(action.side).name,
                    tool=action.tool,
                    args=action.args,
                    text=action.args.get("message", ""),
                    # The map draws the salvo off this event, so it is told here — before
                    # anything flies — exactly how many rounds the defender's batteries
                    # are going to kill. The renderer must never roll its own dice for
                    # this: what dies on screen has to be what died in the arithmetic.
                    shot=(
                        engine.strike_interception(
                            self.state, action.side, str(action.args.get("weapon", ""))
                        )
                        if action.tool == "strike"
                        else None
                    ),
                )
                # The statement goes up and is left alone: no verdict, no numbers, just
                # the claim. Everything that follows is the answer to it.
                await self._dwell(0.4)

                await self.emit(
                    "ruling",
                    side=ruling.side,
                    effective=ruling.effective,
                    modifier=ruling.modifier,
                    reason=ruling.reason,
                    flags=ruling.flags,
                )
                deltas, notes = engine.apply_action(self.state, action, ruling)
                # The war log. Without this an agent cannot tell who hit it or what it
                # already tried, which kills reciprocity and escalation entirely.
                world.history.append(
                    TurnRecord(
                        turn=world.turn, side=action.side, tool=action.tool,
                        args=action.args, effective=ruling.effective, deltas=deltas,
                    )
                )
                if deltas:
                    await self.emit("deltas", side=action.side, tool=action.tool, deltas=deltas)
                for note in notes:
                    world.grievances.append(note)
                    await self.emit("note", text=note)
                await self.emit_state()
                # Ordnance in flight, then fires burning on the map. The rest of this
                # action's slot belongs to watching it land.
                await self._dwell(0.6)

                # A war that is already over does not get a second reply — the loser's
                # capitulation cancels whatever the other side had queued. The one
                # exception is a mutual surrender, where both are heard out.
                if world.phase == "over" and not both_quit:
                    break

            if world.phase != "over":
                # Both capitals have spoken, so a clause now has two positions on it and
                # can be scored. This is where a war ends in a signature rather than a
                # collapse — and, far more often, where the talks stall.
                talk_deltas, talk_notes = engine.resolve_talks(self.state)
                if talk_deltas:
                    await self.emit("deltas", side="world", tool="talks", deltas=talk_deltas)
                for note in talk_notes:
                    world.grievances.append(note)
                    await self.emit("note", text=note)
                if talk_notes:
                    await self.emit("talks", state=world.talks.model_dump())
                    await self.emit_state()
                    await self._dwell(0.5)

            if world.phase != "over":
                world.tension = clamp(world.tension + reaction["tension_delta"])
                # International pressure is nobody's global weather any more — it lands
                # on whichever capital the world decided was at fault this turn.
                if reaction["condemned"]:
                    blamed = self.state.nation(reaction["condemned"])
                    engine.condemn(blamed, reaction["condemnation"])
                engine.apply_upkeep(self.state)

                if reaction["bulletin"]:
                    world.grievances.append(reaction["bulletin"])
                    await self.emit(
                        "bulletin", text=reaction["bulletin"], condemned=reaction["condemned"]
                    )

            await self.emit_state()

            outcome = engine.check_end(self.state)
            if outcome:
                world.phase = "over"
                world.outcome = outcome
                await self.emit("game_over", outcome=outcome, loser=world.loser)
                await self.emit_state()
                if self._file:
                    self._file.close()
                    self._file = None

    async def run(self) -> None:
        """Play the war out. It ends in surrender or collapse — there is no third option."""
        while self.state.world.phase == "conflict":
            await self.step()
            if self.state.world.phase == "conflict":
                await asyncio.sleep(settings.turn_pause)
