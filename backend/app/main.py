"""FastAPI host. One match per websocket connection — this is a local test bench, not a service."""

import asyncio
import json
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .game import Game
from .state import Event

app = FastAPI(title="yudhyantra")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    # `panel` rather than a single model: there are three chairs now, and which model is
    # in which one is the first thing you want to know about a running instance.
    return {
        "ok": True,
        "service": "yudhyantra",
        "mock": settings.use_mock,
        "panel": settings.panel,
    }


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()

    async def emit(ev: Event) -> None:
        await socket.send_text(ev.model_dump_json())

    game = Game(emit)
    runner: Optional[asyncio.Task] = None
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
                if runner and not runner.done():
                    runner.cancel()
            elif command == "inject":
                await game.inject(msg.get("text", ""))
            elif command == "reset":
                if runner and not runner.done():
                    runner.cancel()
                await game.reset()
    except WebSocketDisconnect:
        pass
    finally:
        if runner and not runner.done():
            runner.cancel()
