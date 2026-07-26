"""Regenerate the recordings the replay bench plays.

    MOCK=1 BEAT=0 REVEAL=0 TURN_PAUSE=0 .venv/bin/python -m scripts.make_fixtures

A fixture is an ordinary match log. Nothing here is special-cased on the way back in —
any file `logs/` collects can be dropped into `fixtures/` and replayed — this script just
picks the four wars worth keeping and writes them where the bench looks.

They are chosen by *what they make the UI do*, not by how good a war they are: between
them they have to reach every branch of the frontend at least once, because a branch no
fixture reaches is a branch you can only test by spending money on it.

Two things are trimmed on the way out, both explained below. Everything else is verbatim.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BALANCE_VERSION, settings  # noqa: E402
from app.game import IGNITIONS, Game  # noqa: E402
from app.replay import Recording  # noqa: E402
from app.state import Event  # noqa: E402

OUT = settings.fixtures

# name, seed, cards, what this one is for.
#
# The seeds are not arbitrary and are not interchangeable: they were picked by playing
# sixty seeded matches and taking the shortest one that reached each corner. If the
# balance moves, these will drift — regenerate, re-read the table this script prints, and
# repick if a fixture no longer does its job.
RECIPES: List[Tuple[str, int, List[str], str]] = [
    (
        "accord",
        20,
        ["registry"],
        "the table works: three rounds, five clauses signed, and nobody loses",
    ),
    (
        "nuclear",
        7,
        ["rig"],
        "a warhead, two atrocities and a six-figure toll — the numbers at their largest",
    ),
    (
        "attrition",
        27,
        ["airspace"],
        "the full twelve turns and ten different tools, ending in capitulation",
    ),
    (
        "surrender",
        1,
        ["reef"],
        "somebody quits: the one ending that arrives as a declared move rather than a meter",
    ),
]

# Recordings copied in from `logs/` rather than generated. Live transcripts cannot be
# reproduced from a seed — that is the whole reason to keep one — so this half of the
# directory survives on the committed file alone, and a fresh checkout will skip it.
#
# Named for the models rather than for `live`, which in the picker sits next to the
# option that leaves the bench and starts spending money. Two things called live is one
# too many.
IMPORTS: List[Tuple[str, str, str]] = [
    (
        "nano",
        "match-20260726-172918-816.jsonl",
        "real model prose: gpt-5-nano and gpt-4.1-nano, five turns, cut short at the table",
    ),
]


def trim(ev: Event) -> Event:
    """Drop the one part of a state dump that no pixel is drawn from.

    `world.history` is the agents' memory of the war — every action taken so far, carried
    inside every state event, so a twelve-turn match ships its own transcript twelve
    times over. The frontend has never read it (there is no such field in `types.ts`) and
    the loader refills it with an empty list, so the only thing it costs here is a
    quadratic file. Removing it takes these files from megabytes to kilobytes.
    """
    if ev.type == "state":
        (ev.payload.get("state") or {}).get("world", {}).pop("history", None)
    return ev


def write(path: Path, meta: Dict[str, Any], events: List[Event]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "meta", "turn": 0, "payload": meta}) + "\n")
        for ev in events:
            fh.write(trim(ev).model_dump_json() + "\n")


async def record(name: str, seed: int, cards: List[str], title: str) -> Path:
    game = Game(seed=seed, write_log=False)
    await game.reset()
    await game.ignite(cards)
    await game.run()

    # A real log begins at ignition — the file is not opened until the fuse is lit — and
    # the bench sends its own reset built from today's code. Starting anywhere earlier
    # would have the recording carry a stale copy of the deck.
    start = next(i for i, ev in enumerate(game.log) if ev.type == "ignition")
    meta = {
        "balance_version": BALANCE_VERSION,
        "west_model": "mock",
        "east_model": "mock",
        "arbiter_model": "mock",
        "mock": True,
        "max_turns": settings.max_turns,
        "seed": seed,
        # Not written by `MatchLog`, and optional everywhere it is read: it is the line
        # the picker shows under the name, and a hand-dropped log simply goes without.
        "title": title,
        "cards": [IGNITIONS[c]["label"] for c in cards],
    }
    path = OUT / f"{name}.jsonl"
    write(path, meta, game.log[start:])
    return path


def bring_in(name: str, source: str, title: str) -> Optional[Path]:
    origin = Path(source)
    if not origin.is_absolute():
        origin = settings.log_dir / source
    if not origin.is_file():
        print(f"  {name:<10} skipped — {origin} is not here (logs/ is gitignored)")
        return None
    meta: Dict[str, Any] = {}
    events: List[Event] = []
    for line in origin.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        if raw.get("type") == "meta":
            meta = raw.get("payload") or {}
            continue
        events.append(Event.model_validate(raw))
    meta["title"] = title
    path = OUT / f"{name}.jsonl"
    write(path, meta, events)
    return path


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    print(f"\nwriting recordings to {OUT}\n")
    for name, seed, cards, title in RECIPES:
        written.append(await record(name, seed, cards, title))
    for name, source, title in IMPORTS:
        path = bring_in(name, source, title)
        if path:
            written.append(path)

    print(f"{'name':<10}{'turns':>6}{'events':>8}{'size':>9}  outcome")
    for path in written:
        rec = Recording.load(path)
        size = f"{path.stat().st_size / 1024:.0f}K"
        ending = rec.outcome or "(recording ends mid-war)"
        print(f"{rec.name:<10}{rec.turns:>6}{len(rec.events):>8}{size:>9}  {ending[:52]}")
        # The reason to keep each of these is a UI branch it reaches. If a regenerated
        # fixture stops reaching it, that shows up here rather than three days later.
        marks = sorted({m for cut in rec.cuts for m in cut.marks})
        print(f"{'':<10}{'':>6}{'':>8}{'':>9}  reaches: {', '.join(marks) or 'nothing notable'}")


if __name__ == "__main__":
    asyncio.run(main())
