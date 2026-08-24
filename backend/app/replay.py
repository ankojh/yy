"""Play a recorded match back down the websocket, so working on the UI costs nothing.

The frontend is a pure fold over the event stream. That makes a match log a complete
recording of a session: emit the same events in the same order, at the cadence the live
loop would have used, and the UI cannot tell the difference. No model is called, no key
is needed, and the same war plays identically as many times as you want to look at it.

    REPLAY=nano .venv/bin/uvicorn app.main:app --port 8077
    open http://localhost:5173/?replay=nano&speed=6

Recordings are ordinary match logs — anything `logs/` collects, live or mock, can be
dropped into `fixtures/` and replayed. `scripts/make_fixtures.py` copies in the one that
ships. Two things are deliberately *not* taken from the recording: the ignition deck and
the quarrel, which always come from today's code, so a log recorded before a card existed
still opens the dossiers the current build knows about.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import settings
from .game import reset_payload
from .lore import ACCOUNTS
from .nations import PROFILES
from .state import Event, GameState, initial_state

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# Which moves are worth a line in the jump menu. Every turn has a strike in it; a turn
# where somebody sued for terms or reached for a warhead is the turn you actually want
# to skip to when you are styling the thing that draws it.
NOTABLE: Dict[str, str] = {
    "open_talks": "talks open",
    "table_terms": "terms tabled",
    "accept_terms": "terms accepted",
    "walk_out": "walkout",
    "surrender": "surrender",
    "blockade": "blockade",
}


class Cut:
    """One turn of the war, as the block of events that made it up.

    Turns are the only seams in the stream that mean anything — `step` plays exactly one
    of these, and a jump lands on the boundary between two.
    """

    def __init__(self, turn: int, events: List[Event], marks: Tuple[str, ...]) -> None:
        self.turn = turn
        self.events = events
        self.marks = marks

    @property
    def label(self) -> str:
        return f"turn {self.turn}" + (f" · {' · '.join(self.marks)}" if self.marks else "")


class Recording:
    """A match log, parsed into a prologue and one cut per turn."""

    def __init__(self, name: str, meta: Dict[str, Any], events: List[Event]) -> None:
        self.name = name
        self.meta = meta
        self.prologue: List[Event] = []
        self.cuts: List[Cut] = []

        block: List[Event] = []
        turn = 0
        dead_before = 0
        atrocities_before = 0

        def close(turn: int, block: List[Event]) -> None:
            nonlocal dead_before, atrocities_before
            if not turn:
                self.prologue = block
                return
            marks: List[str] = []
            for ev in block:
                if ev.type == "message":
                    tool = str(ev.payload.get("tool", ""))
                    if tool == "strike" and ev.payload.get("args", {}).get("weapon") == "nuke":
                        marks.append("nuclear")
                    elif tool in NOTABLE:
                        marks.append(NOTABLE[tool])
                elif ev.type == "game_over":
                    marks.append("ends")
            # An atrocity is not a tool call — it is the consequence of one landing on a
            # school. It only shows up as the world's ledger of them growing.
            world = self._last_world(block)
            if world is not None:
                if len(world.get("atrocities") or []) > atrocities_before:
                    marks.append("atrocity")
                atrocities_before = len(world.get("atrocities") or [])
            dead = self._last_toll(block)
            if dead is not None:
                if dead - dead_before >= 10_000:
                    marks.append("mass casualties")
                dead_before = dead
            # `dict.fromkeys` rather than a set: two capitals can table terms in the same
            # turn, and the menu should say that once, in the order it happened.
            self.cuts.append(Cut(turn, block, tuple(dict.fromkeys(marks))))

        for ev in events:
            if ev.type == "turn_started":
                close(turn, block)
                turn = int(ev.payload.get("turn") or ev.turn or turn + 1)
                block = [ev]
                continue
            block.append(ev)
        close(turn, block)

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, path: Path, name: str = "") -> "Recording":
        meta: Dict[str, Any] = {}
        events: List[Event] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                # Every line is flushed as it happens, so a log from a match that was
                # still running when the process died ends in half a line. Stop there
                # rather than refusing to open an otherwise perfectly good recording.
                break
            if raw.get("type") == "meta":
                meta = raw.get("payload") or {}
                continue
            ev = Event.model_validate(raw)
            if ev.type == "state":
                # An old recording predates half the fields today's UI reads. Passing it
                # back through the model fills them with the values a fresh match would
                # have started with, which is the difference between a stale log
                # replaying and a stale log rendering a column of `undefined`.
                state = GameState.model_validate(ev.payload.get("state") or {})
                # How a country *describes itself* belongs to the build, not to the
                # match — same rule as the deck and the quarrel. Every state dump in a
                # recording carries a frozen copy of it, so without this a blurb edited
                # today would still read the old way on the bench forever, and the one
                # recording that cannot be regenerated would never catch up at all.
                for side in ("west", "east"):
                    nation = getattr(state, side)
                    nation.blurb = PROFILES[side]["blurb"]
                    nation.creed = ACCOUNTS[side]["creed"]
                ev.payload["state"] = state.model_dump()
            events.append(ev)
        return cls(name or path.stem, meta, events)

    # ------------------------------------------------------------------ reading

    @staticmethod
    def _last_world(block: List[Event]) -> Optional[Dict[str, Any]]:
        for ev in reversed(block):
            if ev.type == "state":
                return (ev.payload.get("state") or {}).get("world") or {}
        return None

    @staticmethod
    def _last_toll(block: List[Event]) -> Optional[int]:
        for ev in reversed(block):
            if ev.type == "state":
                state = ev.payload.get("state") or {}
                return int(state.get("west", {}).get("casualties", 0)) + int(
                    state.get("east", {}).get("casualties", 0)
                )
        return None

    @property
    def events(self) -> List[Event]:
        return self.prologue + [ev for cut in self.cuts for ev in cut.events]

    @property
    def turns(self) -> int:
        return self.cuts[-1].turn if self.cuts else 0

    @property
    def outcome(self) -> str:
        for ev in reversed(self.cuts[-1].events if self.cuts else []):
            if ev.type == "game_over":
                return str(ev.payload.get("outcome") or "")
        return ""

    @property
    def complete(self) -> bool:
        """Whether the war was played to a verdict, or the recording just stops.

        Most of the live transcripts are the second kind — somebody closed the tab. A
        bench that pretends otherwise leaves you staring at a stalled stage wondering
        which of the two it is.
        """
        return bool(self.outcome)

    @property
    def cards(self) -> List[str]:
        for ev in self.prologue:
            if ev.type == "ignition":
                return list(ev.payload.get("cards") or [])
        return []

    @property
    def panel(self) -> Dict[str, str]:
        """The three chairs that actually produced this transcript, not today's."""
        return {
            side: str(self.meta.get(f"{side}_model") or self.meta.get("nation_model") or "mock")
            for side in ("west", "east")
        } | {"arbiter": str(self.meta.get("arbiter_model") or "mock")}

    @property
    def live(self) -> bool:
        return not self.meta.get("mock", True)

    @property
    def summary(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "title": str(self.meta.get("title") or ", ".join(self.cards) or self.name),
            "turns": self.turns,
            "events": sum(len(c.events) for c in self.cuts) + len(self.prologue),
            "outcome": self.outcome,
            "complete": self.complete,
            "live": self.live,
            "panel": self.panel,
            "balance_version": str(self.meta.get("balance_version") or "unknown"),
            "dead": self._last_toll(self.cuts[-1].events) if self.cuts else 0,
            "marks": [{"turn": c.turn, "label": c.label} for c in self.cuts],
        }


# Recordings are a few hundred kilobytes each and are re-read on every new socket, so
# they are cached on mtime: edit one, or regenerate the lot, and the next connection
# picks it up without a restart.
_cache: Dict[Path, Tuple[float, Recording]] = {}


def _read(path: Path) -> Recording:
    stamp = path.stat().st_mtime
    hit = _cache.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    recording = Recording.load(path)
    _cache[path] = (stamp, recording)
    return recording


def catalogue(directory: Optional[Path] = None) -> List[Recording]:
    folder = directory or settings.fixtures
    if not folder.is_dir():
        return []
    return [_read(p) for p in sorted(folder.glob("*.jsonl"))]


def find(name: str, directory: Optional[Path] = None) -> Optional[Recording]:
    """Look a recording up by handle, or by path for anything not in `fixtures/`.

    The name arrives off a query string, so the loose-path branch is limited to files
    that are already `.jsonl` — that is what makes `?replay=logs/match-2026….jsonl` work
    without also making the parameter a way to ask the server to read /etc/anything.
    """
    if not name:
        return None
    folder = directory or settings.fixtures
    path = folder / f"{name}.jsonl"
    if path.is_file():
        return _read(path)
    loose = Path(name)
    if loose.suffix == ".jsonl" and loose.is_file():
        return _read(loose)
    return None


class ReplayGame:
    """Stands in for `Game` behind the websocket: same commands, no models, no spend."""

    def __init__(
        self,
        emit: Optional[Callable[[Event], Any]] = None,
        recording: Optional[Recording] = None,
        speed: float = 1.0,
    ) -> None:
        self.recording = recording or Recording("empty", {}, [])
        self.speed = self._clamp(speed)
        self.cursor = 0        # cuts played so far
        self.started = False   # whether the prologue has run
        self._emit = emit
        self._lock = asyncio.Lock()

    @staticmethod
    def _clamp(speed: float) -> float:
        try:
            return max(0.1, min(64.0, float(speed)))
        except (TypeError, ValueError):
            return 1.0

    # ------------------------------------------------------------------ pacing

    def _hold(self, kind: str, previous: str) -> float:
        """How long the live loop waits after emitting this event.

        Nothing in a match log is timestamped, so the cadence is reconstructed from the
        one place that owns it: the pauses `Game.step` takes between its emissions. The
        two have to be kept in step — a bench that runs to a different rhythm than the
        real thing is a worse place to tune an animation than no bench at all.
        """
        if kind == "turn_started":
            return settings.beat * 0.45
        if kind == "message":
            return settings.reveal * 0.4       # the claim owns the stage, alone
        if kind == "state":
            if previous in ("ruling", "deltas", "note"):
                return settings.reveal * 0.6   # ordnance in flight, then fires burning
            if previous == "talks":
                return settings.reveal * 0.5
        return 0.0

    async def _pause(self, seconds: float) -> None:
        if seconds > 0:
            await asyncio.sleep(seconds / self.speed)

    async def _send(self, ev: Event) -> None:
        if self._emit:
            await self._emit(ev)

    async def _say(self, type_: str, **payload: Any) -> None:
        """A word from the bench itself. Never confusable with a recorded event."""
        await self._send(Event(type=type_, turn=self.recording.turns, payload=payload))

    async def _play(self, events: List[Event], paced: bool = True) -> None:
        previous = ""
        for ev in events:
            await self._send(ev)
            if paced:
                await self._pause(self._hold(ev.type, previous))
            previous = ev.type

    # ------------------------------------------------------------------ commands

    async def reset(self) -> None:
        self.cursor = 0
        self.started = False
        payload = reset_payload()
        payload.update(
            # Nothing is being spent, but "mock" is a claim about the models that played
            # the match, and for a live recording that claim would be a lie.
            mock=not self.recording.live,
            panel=self.recording.panel,
            replay=self.block(),
            dev_view_available=False,
        )
        payload["reveal_ms"] = int(payload["reveal_ms"] / self.speed)
        await self._send(Event(type="reset", payload=payload))
        await self._send(Event(type="state", payload={"state": initial_state().model_dump()}))

    def block(self) -> Dict[str, Any]:
        """The bench's own state, for the replay bar: what is loaded, what else there is."""
        return {
            "name": self.recording.name,
            "speed": self.speed,
            "current": self.recording.summary,
            "available": [r.summary for r in catalogue()],
        }

    async def ignite(self, ids: Optional[List[str]] = None, custom: str = "") -> None:
        """The opening nudge only starts the recorded prologue here.

        A war that has already been fought cannot be started differently, so whatever is
        selected under the hood, the prologue that plays is the recorded one.
        """
        if self.started:
            return
        self.started = True
        await self._play(self.recording.prologue)

    async def configure(self, setup: Dict[str, Any]) -> None:
        """No-op. The opening positions are whatever they were on the day."""
        return None

    async def step(self) -> None:
        async with self._lock:
            await self._advance()

    async def _advance(self) -> bool:
        if not self.started:
            self.started = True
            await self._play(self.recording.prologue)
            return True
        if self.cursor >= len(self.recording.cuts):
            return False
        cut = self.recording.cuts[self.cursor]
        self.cursor += 1
        await self._play(cut.events)
        return True

    async def run(self) -> None:
        while self.cursor < len(self.recording.cuts):
            await self.step()
            if self.cursor < len(self.recording.cuts):
                await self._pause(settings.turn_pause)
        if not self.recording.complete:
            await self._say(
                "note",
                text="— the recording ends here. This match was never played to a verdict. —",
            )

    async def inject(self, text: str) -> None:
        """Nothing can be injected into a war that has already been fought.

        The event still goes on the stage, because the ticker branch that renders it
        needs working on like everything else — and is then immediately contradicted, so
        nobody spends ten minutes wondering why the meters did not move.
        """
        if not text.strip():
            return
        await self._say("injection", text=text.strip()[:500])
        await self._say("note", text="— a recording cannot be injected into; nothing moved —")

    async def support(self, request_id: str, approved: bool) -> None:
        """A recording cannot be altered by a new council grant."""
        if not request_id:
            return
        await self._say("note", text="— a recording cannot receive new council support —")

    async def seek(self, turn: int) -> None:
        """Jump to the war as it stood at the end of a given turn.

        The point of the whole bench. You are styling the talks board; the talks open on
        turn six. Rebuilding from the top is the only correct way to get there — the UI
        is a fold, so its state at turn six is precisely the events up to turn six — but
        it is done at full speed, behind a `scrub` flag, so eleven salvos do not fly
        across the strait on the way past.
        """
        async with self._lock:
            await self._say("scrub", active=True)
            try:
                await self.reset()
                self.started = True
                await self._play(self.recording.prologue, paced=False)
                played = 0
                for cut in self.recording.cuts:
                    if cut.turn > turn:
                        break
                    await self._play(cut.events, paced=False)
                    played += 1
                self.cursor = played
            finally:
                # A half-scrubbed UI with the flag still up would silently swallow every
                # animation for the rest of the session.
                await self._say("scrub", active=False)

    async def set_speed(self, speed: float) -> None:
        self.speed = self._clamp(speed)
        # Bubbles and tooltips hold for as long as the server says a beat lasts, and that
        # number was sent on reset. Changing the rate without resending it leaves text on
        # screen for eleven seconds of a war running at six times speed.
        await self._say(
            "pace", reveal_ms=int(settings.reveal * 1000 / self.speed), speed=self.speed
        )
