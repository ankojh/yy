"""The replay bench.

Two jobs here. The first is the bench itself: does a recording split into turns, does
playing it emit exactly what was recorded, does a jump land in the same place stepping
there would have.

The second matters more and is the reason the fixtures are committed rather than
generated on demand. They are the only thing in the repo that pins the *shape* of a state
dump — the frontend reads twenty-odd fields off it and typescript cannot check a single
one of them across the wire. A field renamed in `state.py` breaks the UI silently and
these are what catch it.
"""

import json

import pytest

from app.config import settings
from app.game import IGNITIONS, Game
from app.lore import ACCOUNTS
from app.nations import PROFILES
from app.replay import Recording, ReplayGame, catalogue, find
from app.state import Event, GameState

FIXTURES = [r.name for r in catalogue()]


def collect():
    """A recorder standing in for the websocket."""
    seen = []

    async def emit(ev: Event) -> None:
        seen.append(ev)

    return seen, emit


# --------------------------------------------------------------------- the fixtures


def test_there_are_recordings_to_replay():
    assert FIXTURES, "fixtures/ is empty — run scripts/make_fixtures.py"


@pytest.mark.parametrize("name", FIXTURES)
def test_every_state_in_every_recording_still_parses(name):
    """The canary for schema drift.

    `Recording.load` fills missing fields with defaults, which is what lets an old log
    replay at all — so this reads the raw lines instead. A recording whose states no
    longer round-trip cleanly is a recording the UI is now rendering blanks from.
    """
    path = settings.fixtures / f"{name}.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        if raw.get("type") != "state":
            continue
        stored = raw["payload"]["state"]
        parsed = GameState.model_validate(stored).model_dump()
        # `history` is stripped on the way out — it is the agents' memory, no pixel is
        # drawn from it, and carrying it makes the files quadratic.
        missing = {
            key for key in parsed["world"] if key not in stored["world"] and key != "history"
        }
        assert not missing, f"{name}: world is missing {missing}"
        for side in ("west", "east"):
            gone = {key for key in parsed[side] if key not in stored[side]}
            assert not gone, f"{name}: {side} is missing {gone}"


@pytest.mark.parametrize("name", FIXTURES)
def test_every_recording_is_a_war(name):
    rec = find(name)
    assert rec is not None
    assert rec.turns >= 1
    assert rec.cuts, "no turns"
    assert any(ev.type == "ignition" for ev in rec.prologue)
    assert rec.panel["west"] and rec.panel["east"] and rec.panel["arbiter"]


def test_the_deck_between_them_reaches_every_branch_the_ui_draws():
    """A tool no fixture reaches is a tool you can only look at by paying for it.

    `civilian` is the deliberate gap: the scripted commander never picks that target, so
    no seeded recording can cover the protected-place branch. It is reachable live, and
    it is the one thing on this bench you still have to spend money to see.
    """
    tools = {
        ev.payload.get("tool")
        for rec in catalogue()
        for ev in rec.events
        if ev.type == "message"
    }
    expected = {
        "strike", "blockade", "fortify", "intl_appeal", "address_public", "propaganda",
        "open_talks", "table_terms", "accept_terms", "walk_out", "surrender", "hold",
    }
    assert expected - tools == set(), f"no recording reaches {expected - tools}"

    weapons = {
        ev.payload.get("args", {}).get("weapon")
        for rec in catalogue()
        for ev in rec.events
        if ev.type == "message" and ev.payload.get("tool") == "strike"
    }
    assert weapons >= {"drone_swarm", "cruise_missile", "naval_barrage", "cyber_strike", "nuke"}


@pytest.mark.parametrize("name", FIXTURES)
def test_how_a_country_describes_itself_comes_from_the_build_not_the_recording(name):
    """Every state dump carries a frozen copy of the two blurbs. Edit one and the bench
    would otherwise keep reading the old text back at you — permanently, for `nano`,
    which cannot be regenerated from a seed."""
    rec = find(name)
    for ev in rec.events:
        if ev.type != "state":
            continue
        for side in ("west", "east"):
            assert ev.payload["state"][side]["blurb"] == PROFILES[side]["blurb"]
            assert ev.payload["state"][side]["creed"] == ACCOUNTS[side]["creed"]


def test_a_recording_that_was_played_out_says_so_and_one_that_was_not_does_not():
    assert find("accord").complete and "Accord" in find("accord").outcome
    assert not find("nano").complete and find("nano").outcome == ""


def test_the_live_recording_is_actually_live():
    """The reason to keep it. Mock prose is written by a `%s` format string; this is the
    only fixture whose bubbles are real model output, and it is the one that finds the
    layout bugs a two-clause canned sentence never will."""
    nano = find("nano")
    assert nano.live
    assert nano.panel["west"] != "mock" and nano.panel["east"] != "mock"
    said = [ev.payload["text"] for ev in nano.events if ev.type == "message"]
    assert said and max(len(t) for t in said) > 60


# --------------------------------------------------------------------- turns and marks


def test_a_recording_splits_on_turns_and_loses_nothing():
    rec = find("attrition")
    assert [c.turn for c in rec.cuts] == list(range(1, rec.turns + 1))
    for cut in rec.cuts:
        assert cut.events[0].type == "turn_started"
    assert len(rec.events) == len(rec.prologue) + sum(len(c.events) for c in rec.cuts)


def test_the_jump_menu_names_the_turns_worth_jumping_to():
    marks = {m for cut in find("nuclear").cuts for m in cut.marks}
    assert "nuclear" in marks
    assert "mass casualties" in marks
    assert "ends" in marks
    # Every turn has a strike in it. Naming that would make the menu useless.
    assert "strike" not in marks


def test_an_unknown_name_is_not_a_recording():
    assert find("no-such-war") is None
    assert find("") is None


def test_a_truncated_last_line_does_not_lose_the_recording(tmp_path):
    """Every line is flushed as it happens, so a log from a process that died mid-match
    ends in half a line of JSON. That is the common case for a live transcript, not a
    corrupt file."""
    whole = (settings.fixtures / "surrender.jsonl").read_text(encoding="utf-8")
    torn = tmp_path / "torn.jsonl"
    torn.write_text(whole[: int(len(whole) * 0.6)], encoding="utf-8")
    rec = Recording.load(torn)
    assert rec.turns >= 2
    assert not rec.complete


# --------------------------------------------------------------------- playing it back


@pytest.mark.asyncio
async def test_running_a_recording_emits_exactly_what_was_recorded():
    rec = find("accord")
    seen, emit = collect()
    bench = ReplayGame(emit, rec)
    await bench.reset()
    await bench.ignite([])
    await bench.run()

    # The bench sends its own reset and its own opening state, built from today's code —
    # everything after that is the recording, verbatim and in order.
    assert [ev.type for ev in seen[:2]] == ["reset", "state"]
    assert [ev.model_dump() for ev in seen[2:]] == [ev.model_dump() for ev in rec.events]


@pytest.mark.asyncio
async def test_the_reset_carries_todays_deck_over_an_old_recording():
    """A recording made before a card existed still has to open that card's dossier.

    The deck and the quarrel come from the running code, never from the transcript;
    only the three chairs are read back off the log, because those are a fact about the
    match rather than about the build.
    """
    seen, emit = collect()
    bench = ReplayGame(emit, find("nano"))
    await bench.reset()
    payload = seen[0].payload
    assert len(payload["ignitions"]) == len(IGNITIONS)
    assert payload["articles"] and payload["partition"]
    assert payload["panel"]["west"] == "gpt-5-nano"     # from the log, not from settings
    assert payload["mock"] is False
    assert payload["replay"]["name"] == "nano"
    assert [r["name"] for r in payload["replay"]["available"]] == sorted(FIXTURES)


@pytest.mark.asyncio
async def test_stepping_plays_one_turn_at_a_time():
    rec = find("surrender")
    seen, emit = collect()
    bench = ReplayGame(emit, rec)
    await bench.reset()
    await bench.ignite([])
    before = len(seen)
    await bench.step()
    assert [ev.model_dump() for ev in seen[before:]] == [
        ev.model_dump() for ev in rec.cuts[0].events
    ]
    assert bench.cursor == 1


@pytest.mark.asyncio
async def test_stepping_off_the_end_does_nothing_rather_than_failing():
    _, emit = collect()
    bench = ReplayGame(emit, find("nano"))
    await bench.reset()
    await bench.run()
    at_end = bench.cursor
    await bench.step()
    assert bench.cursor == at_end


@pytest.mark.asyncio
async def test_a_recording_that_stops_mid_war_says_so_at_the_end():
    """Otherwise the stage simply stops and you spend ten minutes deciding whether the
    bench has hung or the war has."""
    seen, emit = collect()
    bench = ReplayGame(emit, find("nano"))
    await bench.reset()
    await bench.run()
    assert "never played to a verdict" in seen[-1].payload["text"]


@pytest.mark.asyncio
async def test_a_jump_lands_where_stepping_there_would_have():
    """The claim the whole feature rests on: the UI is a fold over the stream, so the
    state at turn six is the events up to turn six and nothing else. If a jump produced
    a different final state than stepping, every screenshot taken off the bench would be
    of a war that never happened."""
    stepped, emit_a = collect()
    walk = ReplayGame(emit_a, find("attrition"))
    await walk.reset()
    await walk.ignite([])
    for _ in range(6):
        await walk.step()

    jumped, emit_b = collect()
    leap = ReplayGame(emit_b, find("attrition"))
    await leap.reset()
    await leap.seek(6)

    last = lambda log: [e for e in log if e.type == "state"][-1].payload["state"]  # noqa: E731
    assert last(jumped) == last(stepped)
    assert leap.cursor == walk.cursor


@pytest.mark.asyncio
async def test_a_jump_is_wrapped_in_a_flag_the_ui_can_see():
    """The frontend suppresses thirty seconds of animation on the strength of this pair.
    An unclosed flag would silently swallow every bubble for the rest of the session."""
    seen, emit = collect()
    bench = ReplayGame(emit, find("nuclear"))
    await bench.reset()
    await bench.seek(4)
    flags = [ev.payload["active"] for ev in seen if ev.type == "scrub"]
    assert flags == [True, False]


@pytest.mark.asyncio
async def test_jumping_backwards_rebuilds_rather_than_rewinds():
    seen, emit = collect()
    bench = ReplayGame(emit, find("attrition"))
    await bench.reset()
    await bench.seek(9)
    await bench.seek(2)
    assert bench.cursor == 2
    assert [e for e in seen if e.type == "state"][-1].payload["state"]["world"]["turn"] == 2


@pytest.mark.asyncio
async def test_nothing_can_be_injected_into_a_war_that_is_already_over():
    seen, emit = collect()
    bench = ReplayGame(emit, find("accord"))
    await bench.reset()
    await bench.inject("a third nation lands troops")
    assert [ev.type for ev in seen[-2:]] == ["injection", "note"]
    assert "nothing moved" in seen[-1].payload["text"]


@pytest.mark.asyncio
async def test_configuring_a_recording_is_a_no_op_not_an_error():
    seen, emit = collect()
    bench = ReplayGame(emit, find("accord"))
    await bench.reset()
    before = len(seen)
    await bench.configure({"west": {"morale": 5}})
    assert len(seen) == before


@pytest.mark.asyncio
async def test_changing_speed_resends_how_long_a_beat_lasts():
    """Bubbles hold for as long as the server said a beat was, and that was said on
    reset. Quadrupling the rate without resending it leaves text on screen for four
    times the action it belongs to."""
    seen, emit = collect()
    bench = ReplayGame(emit, find("accord"), speed=1)
    await bench.reset()
    await bench.set_speed(4)
    assert bench.speed == 4
    assert seen[-1].type == "pace"
    assert seen[-1].payload["reveal_ms"] == int(settings.reveal * 1000 / 4)


@pytest.mark.asyncio
async def test_speed_cannot_be_set_to_something_that_stops_the_bench():
    bench = ReplayGame(None, find("accord"), speed=0)
    assert bench.speed >= 0.1
    await bench.set_speed("nonsense")   # type: ignore[arg-type]
    assert bench.speed == 1.0
    await bench.set_speed(10_000)
    assert bench.speed <= 64


# --------------------------------------------------------------------- the recipes


@pytest.mark.asyncio
async def test_the_seeded_recordings_are_still_reproducible():
    """The fixtures are committed, but they are only worth committing if the seed that
    made one still makes it. When the balance moves this fails, and the fix is to
    regenerate and re-read the coverage table the script prints — not to delete this.
    """
    rec = find("surrender")
    game = Game(seed=int(rec.meta["seed"]), write_log=False)
    await game.reset()
    await game.ignite([k for k, v in IGNITIONS.items() if v["label"] in rec.meta["cards"]])
    await game.run()
    assert game.state.world.outcome == rec.outcome
    assert game.state.world.turn == rec.turns
