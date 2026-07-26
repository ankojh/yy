import os
from pathlib import Path

from dotenv import load_dotenv

# backend/.env — gitignored, and never read by the frontend.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Bump whenever mechanics or costs change. Stamped into every match log so a
# transcript can be read against the rules that actually produced it.
BALANCE_VERSION = "2026.07.26-b4-talks"

# --------------------------------------------------------------------------- the panel
#
# Three different models, one per chair. One model playing both nations and then also
# judging itself is not a war, it is a model talking to itself: the same priors read the
# same brief, reach for the same tool, and then rule that reaching for it was reasonable.
# Whatever came out of that was a property of the model, not of the balance.
#
# The constraint is that they have to be *interchangeable in price*, or the experiment
# stops being about the models and starts being about who paid more. All three sit in the
# same tier — cents per million tokens, function calling, JSON mode:
#
#   gpt-5-nano     $0.05 / $0.40  per 1M in/out
#   gpt-4.1-nano   $0.10 / $0.40
#
# The part that has to be separated is the two *commanders*: one model reading both
# briefs reaches for the same tool twice and the war stops having two sides. The arbiter
# repeating a family is a much smaller effect and not worth paying triple for, so it
# takes the cheapest seat. It does mean the referee shares a family with Aurelia's
# chair — `SWAP_MODELS=1` moves that model to Korsav's chair, so a paired run scores
# both arrangements and whatever the shared family is worth cancels out between them.
DEFAULT_PANEL = {
    "west": "gpt-5-nano",
    "east": "gpt-4.1-nano",
    "arbiter": "gpt-5-nano",
}


class Settings:
    """Runtime config. Everything is env-driven so there is nothing to edit to switch models."""

    def __init__(self) -> None:
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        # NATION_MODEL, if set, overrides both chairs — the escape hatch for anyone who
        # wants the old symmetric setup back, or a controlled single-model run.
        both = os.getenv("NATION_MODEL", "")
        self.west_model = os.getenv("WEST_MODEL", both or DEFAULT_PANEL["west"])
        self.east_model = os.getenv("EAST_MODEL", both or DEFAULT_PANEL["east"])
        self.arbiter_model = os.getenv("ARBITER_MODEL", DEFAULT_PANEL["arbiter"])
        # Which model sits in which chair is itself a bias: leave it fixed and one model
        # is permanently the rich republic. Flip it and the pairing reverses, so a run of
        # matches can be scored both ways round.
        self.swap_models = os.getenv("SWAP_MODELS", "").lower() in ("1", "true", "yes")
        self.max_turns = int(os.getenv("MAX_TURNS", "12"))
        # Pacing. The loop resolves far faster than anyone can read, and the interesting
        # part of a turn is watching one side's move land before the other answers.
        # `reveal` is how long a single declared action owns the stage, start to finish:
        # the statement goes up, the ordnance flies, the damage lands, and the bubble
        # stays readable for the whole of it. The frontend is told this number so its
        # tooltips live exactly as long as the beat they belong to.
        self.reveal = float(os.getenv("REVEAL", "10.0"))   # seconds per declared action
        self.beat = float(os.getenv("BEAT", "0.6"))        # short punctuation pauses
        self.turn_pause = float(os.getenv("TURN_PAUSE", "4.0"))  # between turns
        self.log_dir = Path(os.getenv("LOG_DIR", Path(__file__).resolve().parent.parent / "logs"))
        self._force_mock = os.getenv("MOCK", "").lower() in ("1", "true", "yes")
        # The replay bench. `REPLAY=<name>` starts every connection on a recorded match
        # instead of a live one, which is how the UI gets worked on without spending
        # anything; `?replay=<name>` on the page URL overrides it per tab, and the picker
        # in the header switches without a restart. Empty means fight it for real.
        self.replay = os.getenv("REPLAY", "")
        self.replay_speed = float(os.getenv("REPLAY_SPEED", "1") or 1)
        self.fixtures = Path(
            os.getenv("FIXTURES", Path(__file__).resolve().parent.parent / "fixtures")
        )

    def model_for(self, side: str) -> str:
        """Which model commands this island. `mock` when nothing is being spent."""
        if self.use_mock:
            return "mock"
        if self.swap_models:
            side = "east" if side == "west" else "west"
        return self.west_model if side == "west" else self.east_model

    @property
    def panel(self) -> dict:
        """The three chairs, for the match log and the UI. Never a secret."""
        return {
            "west": self.model_for("west"),
            "east": self.model_for("east"),
            "arbiter": "mock" if self.use_mock else self.arbiter_model,
        }

    @property
    def use_mock(self) -> bool:
        """No key means no spend: the whole loop runs on scripted agents instead."""
        return self._force_mock or not self.openai_api_key


settings = Settings()
