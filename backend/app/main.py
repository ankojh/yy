"""FastAPI host. One match per websocket connection — this is a local test bench, not a service."""

import asyncio
import json
from typing import Literal, Optional, Union

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import settings
from .game import Game
from .replay import ReplayGame, catalogue, find
from .speech import SpeechService, SpeechUnavailable
from .state import Event

app = FastAPI(title="yudhyantra")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

Bench = Union[Game, ReplayGame]

speech_service = SpeechService(
    settings.cloudflare_account_id,
    settings.cloudflare_api_token,
    {"west": settings.tts_voice_west, "east": settings.tts_voice_east},
    azure_speech_key=settings.azure_speech_key,
    azure_speech_region=settings.azure_speech_region,
    azure_speech_voices={
        "west": settings.azure_speech_voice_west,
        "east": settings.azure_speech_voice_east,
    },
)


class SpeechRequest(BaseModel):
    side: Literal["west", "east"]
    name: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1200)


@app.get("/health")
async def health():
    # Report decision, dialogue, and referee fallbacks independently.
    return {
        "ok": True,
        "service": "yudhyantra",
        "mock": settings.use_mock,
        "decision_mock": settings.use_mock_decisions,
        "dialogue_mock": settings.use_mock_dialogue,
        "arbiter_mock": settings.use_mock_arbiter,
        "panel": settings.panel,
        "replay": settings.replay or None,
        "audio": {
            "music": "cc0-recorded",
            "speech": speech_service.provider,
        },
    }


@app.get("/replays")
async def replays():
    """Every recording the bench can play, without opening a socket to find out."""
    return {"replays": [r.summary for r in catalogue()]}


@app.post("/speech")
async def speech(request: SpeechRequest):
    """Return neural commander speech without exposing the provider token."""
    try:
        audio = await asyncio.to_thread(
            speech_service.synthesize,
            request.side,
            request.name,
            request.text,
        )
    except SpeechUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Speech-Provider": speech_service.provider,
        },
    )


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()

    async def emit(ev: Event) -> None:
        await socket.send_text(ev.model_dump_json())

    # A recording named on the page URL beats the one named in the environment, so one
    # process can serve a live tab and three replay tabs at once.
    wanted = socket.query_params.get("replay", settings.replay)
    speed = socket.query_params.get("speed", settings.replay_speed)
    recording = find(wanted)
    game: Bench = ReplayGame(emit, recording, speed) if recording else Game(emit)

    runner: Optional[asyncio.Task] = None

    def stop() -> None:
        if runner and not runner.done():
            runner.cancel()

    await game.reset()

    try:
        while True:
            raw = await socket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            command = msg.get("command")

            if command == "configure":
                await game.configure(msg.get("setup") or {})
            elif command == "dev_view" and isinstance(game, Game):
                await game.set_dev_view(msg.get("enabled") is True)
            elif command == "ignite":
                await game.ignite(msg.get("ignitions", []), msg.get("custom", ""))
            elif command == "step":
                await game.step()
            elif command == "run":
                if runner is None or runner.done():
                    runner = asyncio.create_task(game.run())
            elif command == "pause":
                stop()
            elif command == "inject":
                await game.inject(msg.get("text", ""))
            elif command == "support":
                await game.support(
                    str(msg.get("request_id") or ""), msg.get("approved") is True
                )
            elif command == "reset":
                stop()
                await game.reset()
            # ---- the bench. Switching between a recording and a live match is a
            # decision about whether this session spends money, so it is a deliberate
            # command rather than something a stray reconnect can do on its own.
            elif command == "replay":
                stop()
                picked = find(str(msg.get("name") or ""))
                game = (
                    ReplayGame(emit, picked, msg.get("speed", speed))
                    if picked
                    else Game(emit)
                )
                await game.reset()
            elif command == "seek" and isinstance(game, ReplayGame):
                stop()
                await game.seek(int(msg.get("turn") or 0))
            elif command == "speed" and isinstance(game, ReplayGame):
                await game.set_speed(msg.get("value") or 1)
    except WebSocketDisconnect:
        pass
    finally:
        stop()
