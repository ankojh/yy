from email.message import Message
from urllib.error import URLError

import pytest

from app.speech import SpeechService, SpeechUnavailable


class FakeResponse:
    def __init__(self, audio: bytes):
        self.audio = audio
        self.headers = Message()
        self.headers["Content-Type"] = "audio/mpeg"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.audio


def test_unconfigured_speech_never_calls_the_network():
    service = SpeechService("", "", {"west": "atlas", "east": "jupiter"})
    with pytest.raises(SpeechUnavailable, match="not configured"):
        service.synthesize("west", "Aurelia", "Hold the line.")


def test_identical_lines_are_synthesized_once(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(b"ID3-neural-speech")

    monkeypatch.setattr("app.speech.urlopen", fake_urlopen)
    service = SpeechService(
        "account", "secret", {"west": "atlas", "east": "jupiter"}
    )

    first = service.synthesize("west", "Aurelia", "Hold the line.")
    second = service.synthesize("west", "Aurelia", "Hold the line.")

    assert first == second == b"ID3-neural-speech"
    assert len(calls) == 1
    assert calls[0][0].get_header("Authorization") == "Bearer secret"
    assert b'"speaker": "atlas"' in calls[0][0].data


def test_azure_is_preferred_and_receives_xml_safe_aggressive_ssml(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(b"ID3-aggressive-speech")

    monkeypatch.setattr("app.speech.urlopen", fake_urlopen)
    service = SpeechService(
        "cf-account",
        "cf-secret",
        {"west": "atlas", "east": "jupiter"},
        azure_speech_key="azure-secret",
        azure_speech_region="northcentralus",
        azure_speech_voices={
            "west": "en-US-JennyNeural",
            "east": "en-US-GuyNeural",
        },
    )

    assert service.provider == "azure-speech"
    assert service.synthesize("east", "Korsav", "Advance & win.") == b"ID3-aggressive-speech"
    request = calls[0][0]
    assert request.full_url == (
        "https://northcentralus.tts.speech.microsoft.com/cognitiveservices/v1"
    )
    assert request.get_header("Ocp-apim-subscription-key") == "azure-secret"
    assert request.get_header("X-microsoft-outputformat") == (
        "audio-16khz-128kbitrate-mono-mp3"
    )
    assert b'<voice name="en-US-GuyNeural">' in request.data
    assert b"<mstts:express-as style='angry'>Advance &amp; win.</mstts:express-as>" in request.data
    assert b"Korsav" not in request.data


def test_cloudflare_is_used_when_azure_is_unavailable(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        if len(calls) == 1:
            raise URLError("azure unavailable")
        return FakeResponse(b"ID3-cloudflare-fallback")

    monkeypatch.setattr("app.speech.urlopen", fake_urlopen)
    service = SpeechService(
        "cf-account",
        "cf-secret",
        {"west": "atlas", "east": "jupiter"},
        azure_speech_key="azure-secret",
        azure_speech_region="northcentralus",
        azure_speech_voices={
            "west": "en-US-JennyNeural",
            "east": "en-US-GuyNeural",
        },
    )

    assert service.synthesize("west", "Aurelia", "Hold.") == b"ID3-cloudflare-fallback"
    assert len(calls) == 2
    assert calls[1][0].get_header("Authorization") == "Bearer cf-secret"
