"""Rebuild the recording the replay bench plays.

    .venv/bin/python -m scripts.make_fixtures

A fixture is an ordinary match log. Nothing here is special-cased on the way back in —
any file `logs/` collects can be dropped into `fixtures/` and replayed — this script just
copies the one worth keeping to where the bench looks, and prints what it reaches.

The bench used to also carry four seeded mock wars, generated here from fixed seeds and
chosen for the UI branches they covered between them. They are gone: the bench is one
real transcript now. The seeds and their coverage table are in the history if that trade
ever needs undoing.

Live transcripts cannot be reproduced from a seed — that is the whole reason to keep one —
so `fixtures/` survives on the committed file alone, and this script is only needed when
swapping in a newer match.

One thing is trimmed on the way out, explained below. Everything else is verbatim.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.replay import Recording  # noqa: E402
from app.state import Event  # noqa: E402

OUT = settings.fixtures

# name, source log, what this one is for.
#
# Named for the models rather than for `live`, which in the picker sits next to the
# option that leaves the bench and starts spending money. Two things called live is one
# too many.
IMPORTS: List[Tuple[str, str, str]] = [
    (
        "nano",
        "match-20260728-043556-292.jsonl",
        "real model prose: gpt-5-nano and gpt-4.1-nano, twelve turns to capitulation",
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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    print(f"\nwriting recordings to {OUT}\n")
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
        # The reason to keep this one is the UI branches it reaches. If a swapped-in
        # recording stops reaching them, that shows up here rather than three days later.
        marks = sorted({m for cut in rec.cuts for m in cut.marks})
        print(f"{'':<10}{'':>6}{'':>8}{'':>9}  reaches: {', '.join(marks) or 'nothing notable'}")


if __name__ == "__main__":
    main()
