import os
from pathlib import Path

from dotenv import load_dotenv

# backend/.env — gitignored, and never read by the frontend.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Bump whenever mechanics or costs change. Stamped into every match log so a
# transcript can be read against the rules that actually produced it.
BALANCE_VERSION = "2026.08.23-b5-intelligence"

# --------------------------------------------------------------------------- the panel
#
# Jev makes every strategic decision. The two OpenAI models are contrasting public
# voices only: neither receives tools or authority to alter Jev's locked action.
DEFAULT_PANEL = {
    "west": "gpt-5-nano",
    "east": "gpt-4.1-nano",
    "arbiter": "jev-latest",
}

class Settings:
    """Runtime config. Everything is env-driven so there is nothing to edit to switch models."""

    def __init__(self) -> None:
        self.app_env = os.getenv("APP_ENV", "development").strip().lower()
        # Raw prompts and model replies are useful on the local test bench, but they can
        # contain the complete hidden game state. Never write them in production unless
        # an operator opts in explicitly.
        debug_default = self.app_env != "production"
        debug_value = os.getenv("LLM_DEBUG", "1" if debug_default else "0").lower()
        self.llm_debug = debug_value in ("1", "true", "yes")
        # Jev chooses island actions and adjudicates them. OpenAI only writes the public
        # declarations. Both keys stay backend-only; either lane falls back independently.
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        self.typesafe_api_key = os.getenv("TYPESAFE_API_KEY", "").strip()
        # Neural speech is deliberately separate from the game models. Provider credentials
        # remain backend-only; the browser calls our /speech proxy.
        self.cloudflare_account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
        self.cloudflare_api_token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
        self.tts_voice_west = os.getenv("TTS_VOICE_WEST", "atlas").strip() or "atlas"
        self.tts_voice_east = os.getenv("TTS_VOICE_EAST", "jupiter").strip() or "jupiter"
        # Azure Speech is preferred when configured; Aura-2 remains the provider fallback.
        # These standard neural voices both support the angry speaking style used by the
        # commander dialogue.
        self.azure_speech_key = os.getenv("AZURE_SPEECH_KEY", "").strip()
        self.azure_speech_region = os.getenv("AZURE_SPEECH_REGION", "").strip().lower()
        self.azure_speech_voice_west = (
            os.getenv("AZURE_SPEECH_VOICE_WEST", "en-US-JennyNeural").strip()
            or "en-US-JennyNeural"
        )
        self.azure_speech_voice_east = (
            os.getenv("AZURE_SPEECH_VOICE_EAST", "en-US-GuyNeural").strip()
            or "en-US-GuyNeural"
        )
        # NATION_MODEL, if set, gives both islands the same dialogue voice.
        both = os.getenv("NATION_MODEL", "")
        self.west_model = os.getenv("WEST_MODEL", both or DEFAULT_PANEL["west"])
        self.east_model = os.getenv("EAST_MODEL", both or DEFAULT_PANEL["east"])
        self.jev_model = os.getenv("JEV_MODEL", DEFAULT_PANEL["arbiter"])
        # Flip which public voice speaks for each island without changing Jev's decisions.
        self.swap_models = os.getenv("SWAP_MODELS", "").lower() in ("1", "true", "yes")
        self.max_turns = int(os.getenv("MAX_TURNS", "12"))
        # Pacing. The loop resolves far faster than anyone can read, and the interesting
        # part of a turn is watching one side's move land before the other answers.
        # `reveal` is how long a single declared action owns the stage, start to finish:
        # the statement goes up, the ordnance flies, the damage lands, and the bubble
        # stays readable for the whole of it. The frontend is told this number so its
        # tooltips live exactly as long as the beat they belong to.
        self.reveal = float(os.getenv("REVEAL", "18.0"))   # seconds per declared action
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
        """Which OpenAI model voices this island after Jev locks its decision."""
        if self.use_mock_dialogue:
            return "mock"
        if self.swap_models:
            side = "east" if side == "west" else "west"
        return self.west_model if side == "west" else self.east_model

    @property
    def panel(self) -> dict:
        """The two dialogue voices and typed referee, for logs and the UI."""
        return {
            "west": self.model_for("west"),
            "east": self.model_for("east"),
            "arbiter": "mock" if self.use_mock_arbiter else self.jev_model,
        }

    @property
    def use_mock(self) -> bool:
        """True when neither hosted decision nor dialogue generation can run."""
        return self._force_mock or (not self.openai_api_key and not self.typesafe_api_key)

    @property
    def force_mock(self) -> bool:
        return self._force_mock

    @property
    def use_mock_decisions(self) -> bool:
        """Without TypeSafe, island actions fall back to the scripted policy."""
        return self._force_mock or not self.typesafe_api_key

    @property
    def use_mock_dialogue(self) -> bool:
        """Without OpenAI, locked actions receive deterministic declarations."""
        return self._force_mock or not self.openai_api_key

    @property
    def use_mock_arbiter(self) -> bool:
        """Without a TypeSafe key, the referee uses its bounded local fallback."""
        return self._force_mock or not self.typesafe_api_key

    @property
    def use_neural_tts(self) -> bool:
        azure = bool(self.azure_speech_key and self.azure_speech_region)
        cloudflare = bool(self.cloudflare_account_id and self.cloudflare_api_token)
        return azure or cloudflare


settings = Settings()
