"""Cached Cloudflare Aura-2 speech synthesis.

The browser never sees provider credentials. Identical replay lines share the same bytes,
which keeps the free allocation useful instead of regenerating speech after every seek.
"""

import hashlib
import json
from collections import OrderedDict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class SpeechUnavailable(RuntimeError):
    """The provider is unconfigured or could not synthesize this line."""


class SpeechService:
    CLOUDFLARE_MODEL = "@cf/deepgram/aura-2-en"
    ELEVEN_MODEL = "eleven_v3"

    def __init__(
        self,
        account_id: str,
        api_token: str,
        voices: dict[str, str],
        cache_size: int = 128,
        elevenlabs_api_key: str = "",
        elevenlabs_voices: dict[str, str] | None = None,
    ) -> None:
        self.account_id = account_id
        self.api_token = api_token
        self.voices = voices
        self.cache_size = cache_size
        self.elevenlabs_api_key = elevenlabs_api_key
        self.elevenlabs_voices = elevenlabs_voices or {"west": "", "east": ""}
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    @property
    def configured(self) -> bool:
        return self.elevenlabs_configured or self.cloudflare_configured

    @property
    def elevenlabs_configured(self) -> bool:
        return bool(
            self.elevenlabs_api_key
            and self.elevenlabs_voices.get("west")
            and self.elevenlabs_voices.get("east")
        )

    @property
    def cloudflare_configured(self) -> bool:
        return bool(self.account_id and self.api_token)

    @property
    def provider(self) -> str:
        if self.elevenlabs_configured:
            return "elevenlabs-v3"
        if self.cloudflare_configured:
            return "cloudflare-aura-2"
        return "unconfigured"

    def synthesize(self, side: str, name: str, text: str) -> bytes:
        if not self.configured:
            raise SpeechUnavailable("neural speech is not configured")

        # `name` remains request metadata for the UI/API contract, but the two configured
        # voices already identify the speaker. Narrating the island name before every line
        # makes an exchange sound like a screen reader rather than two commanders arguing.
        _ = name
        spoken = text.strip()
        provider = self.provider
        voice = (
            self.elevenlabs_voices.get(side, self.elevenlabs_voices.get("west", ""))
            if provider == "elevenlabs-v3"
            else self.voices.get(side, self.voices["west"])
        )
        key = hashlib.sha256(f"{provider}\0{voice}\0{spoken}".encode()).hexdigest()
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached

        if provider == "elevenlabs-v3":
            try:
                audio = self._elevenlabs(voice, spoken)
            except SpeechUnavailable:
                if not self.cloudflare_configured:
                    raise
                audio = self._cloudflare(side, spoken)
        else:
            audio = self._cloudflare(side, spoken)
        if not audio:
            raise SpeechUnavailable("neural speech provider returned no audio")

        self._cache[key] = audio
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        return audio

    def _elevenlabs(self, voice: str, spoken: str) -> bytes:
        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
            "?output_format=mp3_44100_128"
        )
        # Eleven v3 interprets these as direction, not as words to narrate.
        payload = json.dumps(
            {"text": f"[angry] [shouts] {spoken}", "model_id": self.ELEVEN_MODEL}
        ).encode()
        request = Request(
            url,
            data=payload,
            method="POST",
            headers={
                "xi-api-key": self.elevenlabs_api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
        )
        return self._request(request)

    def _cloudflare(self, side: str, spoken: str) -> bytes:
        if not self.cloudflare_configured:
            raise SpeechUnavailable("neural speech is not configured")
        voice = self.voices.get(side, self.voices["west"])
        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self.account_id}/ai/run/{self.CLOUDFLARE_MODEL}"
        )
        payload = json.dumps(
            {"text": spoken, "speaker": voice, "encoding": "mp3"}
        ).encode()
        request = Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
        )
        return self._request(request)

    @staticmethod
    def _request(request: Request) -> bytes:
        try:
            with urlopen(request, timeout=25) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            raise SpeechUnavailable("neural speech provider request failed") from exc
