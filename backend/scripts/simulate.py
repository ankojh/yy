"""Batch-run seeded mock matches and report the balance.

    MOCK=1 BEAT=0 TURN_PAUSE=0 .venv/bin/python -m scripts.simulate 20

Every match is seeded, so a run is reproducible and a balance change shows up as a
difference in the table rather than as noise.
"""

import asyncio
import collections
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BALANCE_VERSION, settings  # noqa: E402
from app.game import IGNITIONS, Game  # noqa: E402

CARDS = list(IGNITIONS)


async def one(seed: int, keep_log: bool = False):
    """One full match. Only the sample match writes a transcript."""
    game = Game(seed=seed, write_log=keep_log)
    await game.reset()
    await game.ignite([CARDS[seed % len(CARDS)]])
    await game.run()
    return game


FINAL_METERS = ("integrity", "morale", "standing", "gdp", "budget", "intl_pressure", "unrest")


async def main(count: int) -> None:
    actions = collections.Counter()
    weapons = collections.Counter()
    targets = collections.Counter()
    modifiers = collections.Counter()
    flags = collections.Counter()
    sources = collections.Counter()
    endings = collections.Counter()
    losers = collections.Counter()
    # The asymmetry is only real if it shows up here: two nations that end their wars
    # in the same condition are one nation with two names.
    finals = {side: collections.defaultdict(list) for side in ("west", "east")}
    talks = collections.Counter()
    lengths, nukes, streak_blocks, broke = [], 0, 0, collections.Counter()

    for seed in range(count):
        game = await one(seed, keep_log=(seed == 0))
        world = game.state.world
        lengths.append(world.turn)
        losers[world.loser or "neither"] += 1
        # A mechanic nobody reaches for is a mechanic that is not there. This is the row
        # that says whether the table is worth having.
        talks["matches with a ceasefire"] += world.talks_held > 0
        talks["rounds at the table"] += world.talks_held
        talks["clauses signed"] += len(world.talks.settled)
        for side in ("west", "east"):
            nation = game.state.nation(side)
            for meter in FINAL_METERS:
                finals[side][meter].append(getattr(nation, meter))
            if nation.effects.deficit_turns > 0:
                broke[side] += 1

        for ev in game.log:
            p = ev.payload
            if ev.type == "message":
                actions[p["tool"]] += 1
                if p["tool"] == "strike":
                    weapons[p["args"].get("weapon")] += 1
                    targets[p["args"].get("target")] += 1
                    if p["args"].get("weapon") == "nuke":
                        nukes += 1
            elif ev.type == "ruling":
                modifiers[p.get("modifier", 0)] += 1
                for name, on in (p.get("flags") or {}).items():
                    if on and name != "coherent":
                        flags[name] += 1
                if not p.get("effective"):
                    flags["incoherent"] += 1
            elif ev.type == "decision":
                sources[p.get("source")] += 1
                if "strike" not in (p.get("legal") or []):
                    streak_blocks += 1

        outcome = world.outcome or ""
        # Checked first: a settlement's text mentions neither surrender nor collapse, but
        # bucketing it by keyword after them would be one edit away from being wrong.
        if "Strait Accord" in outcome:
            endings["negotiated settlement"] += 1
        elif "surrender" in outcome or "lays down" in outcome or "capitulate" in outcome:
            endings["surrender/capitulation"] += 1
        elif "Nobody won" in outcome or "no one is left" in outcome:
            endings["mutual ruin"] += 1
        else:
            endings["collapse"] += 1

    total = sum(actions.values())
    print(f"\n=== {count} seeded mock matches · balance {BALANCE_VERSION} ===")
    print(f"max_turns={settings.max_turns}  mock={settings.use_mock}\n")

    print(f"action distribution ({total} actions)")
    for tool, n in actions.most_common():
        print(f"  {tool:<16} {n:>4}  {100 * n / total:5.1f}%  {'█' * round(40 * n / total)}")

    print(f"\nmatch length: mean {statistics.mean(lengths):.1f}  "
          f"median {statistics.median(lengths)}  range {min(lengths)}–{max(lengths)}")
    print(f"nuclear use: {nukes} across {count} matches "
          f"({100 * nukes / max(1, count):.0f}% of matches, {100 * nukes / max(1, total):.1f}% of actions)")
    print(f"strike withdrawn by fatigue: {streak_blocks} decision points")

    print("\nfinal condition (mean across matches)")
    print(f"  {'':<10}" + "".join(f"{m[:9]:>10}" for m in FINAL_METERS))
    for side, label in (("west", "Aurelia"), ("east", "Korsav")):
        row = "".join(f"{statistics.mean(finals[side][m]):>10.0f}" for m in FINAL_METERS)
        print(f"  {label:<10}{row}")
    print(f"  bankrupt at the end: {dict(broke) or 'neither, ever'}")

    print("\nwho lost:", dict(losers))
    print("endings:", dict(endings))
    print("talks:", dict(talks))
    print("strike weapons:", dict(weapons))
    print("strike targets:", dict(targets))
    print("arbiter modifiers:", dict(sorted(modifiers.items())))
    print("arbiter flags fired:", dict(flags))
    print("decision sources:", dict(sources))
    print(f"\nsample transcript: {'logs/ (seed 0)'}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 20))
