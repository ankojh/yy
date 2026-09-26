"""Small async client for TypeSafe AI's Jev decision endpoint.

Jev has two typed jobs in this application: choose each island's legal action from its
private state, and judge both declared actions for the neutral Arbiter. The game engine
still owns every numeric effect, and this module never produces commander prose.
"""

import asyncio
import json
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ENDPOINT = "https://api.typesafe.ai/v1/systemone"


class JevUnavailable(RuntimeError):
    """The Jev service is unconfigured, unavailable, or returned an invalid response."""

    def __init__(
        self, message: str, *, status_code: int | None = None, body: str = ""
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


async def decide(
    *,
    api_key: str,
    model: str,
    state: Dict[str, Any],
    questions: Dict[str, Any],
    timeout: float = 12.0,
) -> Dict[str, Any]:
    """Submit one typed decision request without blocking the websocket event loop."""
    if not api_key:
        raise JevUnavailable("TypeSafe API key is not configured")
    return await asyncio.to_thread(
        _request,
        api_key=api_key,
        model=model,
        state=state,
        questions=questions,
        timeout=timeout,
    )


def _request(
    *,
    api_key: str,
    model: str,
    state: Dict[str, Any],
    questions: Dict[str, Any],
    timeout: float,
) -> Dict[str, Any]:
    payload = {"model": model, "state": state, "questions": questions}
    request = Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise JevUnavailable(
            "Jev request failed", status_code=exc.code, body=body[:2000]
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise JevUnavailable("Jev request failed") from exc

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise JevUnavailable("Jev returned invalid JSON") from exc
    if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
        raise JevUnavailable("Jev response contained no typed answers")
    return result
