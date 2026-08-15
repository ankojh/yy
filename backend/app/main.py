"""FastAPI host. One match per websocket connection — this is a local test bench, not a service."""

import asyncio
import json
from typing import Optional, Union

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .game import Game
from .replay import ReplayGame, catalogue, find
from .state import Event

app = FastAPI(title="yudhyantra")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

Bench = Union[Game, ReplayGame]


@app.get("/health")
async def health():
    # `panel` rather than a single model: there are three chairs now, and which model is
    # in which one is the first thing you want to know about a running instance.
    return {
        "ok": True,
        "service": "yudhyantra",
        "mock": settings.use_mock,
        "panel": settings.panel,
        "replay": settings.replay or None,
    }


@app.get("/replays")
async def replays():
    """Every recording the bench can play, without opening a socket to find out."""
    return {"replays": [r.summary for r in catalogue()]}


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
