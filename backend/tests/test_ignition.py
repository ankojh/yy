"""The casus belli deck.

Each card is a file rather than a headline — the face carries the title and the rest is
read on demand — so the shape of the data matters as much as the numbers in it. And the
effects are applied to a live nation, which is where a card that touches money or output
can quietly hit a clamp meant for a 0–100 meter.
"""

import pytest

from app.engine import BUDGET_CEILING
from app.game import IGNITIONS, Game
from app.state import initial_state


@pytest.mark.parametrize("key", list(IGNITIONS))
def test_every_card_is_a_complete_file(key):
    """A card with no timeline opens an empty dossier, which is worse than no dossier."""
    card = IGNITIONS[key]
    assert card["label"] and len(card["label"]) < 40
    assert len(card["text"]) > 60
    assert len(card["timeline"]) >= 3
    for entry in card["timeline"]:
        assert entry["when"] and entry["what"]
    assert set(card["positions"]) == {"west", "east"}
    for stated in card["positions"].values():
        assert len(stated) > 30


@pytest.mark.parametrize("key", list(IGNITIONS))
def test_every_card_moves_both_capitals_or_says_why(key):
    """An incident that only ever touches one side is a headline, not a casus belli."""
    card = IGNITIONS[key]
    assert card["tension"] > 0
    assert card["effects"], key


@pytest.mark.parametrize("key", list(IGNITIONS))
def test_no_card_writes_a_field_that_is_not_on_a_nation(key):
    """A typo'd field name would set an attribute pydantic ignores and vanish silently."""
    nation = initial_state().west
    for fields in IGNITIONS[key]["effects"].values():
        for field in fields:
            assert hasattr(nation, field), f"{key}: {field}"


def test_the_deck_is_big_enough_to_choose_from():
    assert len(IGNITIONS) >= 10


async def ignite(*cards):
    game = Game(write_log=False)
    await game.reset()
    await game.ignite(list(cards))
    return game.state


@pytest.mark.asyncio
async def test_a_card_that_takes_money_takes_money():
    """The treasury is not a meter and does not stop at a hundred. Korsav opens on 96,
    so before the ceiling was passed through, any budget effect was a point away from
    being silently capped by a clamp meant for morale."""
    state = await ignite("registry")
    opening = initial_state().east.budget
    assert state.east.budget == opening + IGNITIONS["registry"]["effects"]["east"]["budget"]
    assert state.east.budget < opening


@pytest.mark.asyncio
async def test_a_card_that_wrecks_an_economy_lowers_its_ceiling_too():
    """Output recovers towards what the country was worth before the war. A card that
    takes eight points off and leaves the baseline alone is a card that does nothing
    after two upkeeps."""
    state = await ignite("cable")
    assert state.west.gdp_base == state.west.gdp
    assert state.west.gdp < initial_state().west.gdp


@pytest.mark.asyncio
async def test_a_card_that_enriches_one_raises_its_ceiling():
    state = await ignite("rig")
    assert state.west.gdp > initial_state().west.gdp
    assert state.west.gdp_base == state.west.gdp


@pytest.mark.asyncio
async def test_stacking_cards_starts_a_hotter_war():
    one = await ignite("trawler")
    three = await ignite("trawler", "memorial", "rig")
    assert three.world.tension > one.world.tension
    assert len(three.world.grievances) > len(one.world.grievances)


@pytest.mark.asyncio
async def test_the_reset_payload_carries_the_whole_file():
    """The frontend renders the dossier from this and nothing else, so a card that ships
    without its timeline opens a modal with a title and a blank page."""
    game = Game(write_log=False)
    await game.reset()
    payload = next(e for e in game.log if e.type == "reset").payload
    assert len(payload["ignitions"]) == len(IGNITIONS)
    for card in payload["ignitions"]:
        assert card["id"] and card["label"] and card["text"]
        assert card["timeline"] and card["positions"]
