"""TypeSafe Jev transport stays backend-only and validates its response envelope."""

import asyncio
import json

import pytest

from app import jev


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_decide_posts_the_typed_request_with_backend_authorization(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return Response({
            "model": "jev-1.13.0",
            "answers": {"coherent": {"type": "noul", "noul": 0.9}},
            "usage": {"input_tokens": 12, "output_tokens": 2},
        })

    monkeypatch.setattr(jev, "urlopen", fake_urlopen)
    result = asyncio.run(jev.decide(
        api_key="tsf-secret",
        model="jev-latest",
        state={"action": "hold"},
        questions={"coherent": {"type": "noul"}},
    ))

    request = seen["request"]
    assert request.full_url == "https://api.typesafe.ai/v1/systemone"
    assert request.get_header("Authorization") == "Bearer tsf-secret"
    assert json.loads(request.data) == {
        "model": "jev-latest",
        "state": {"action": "hold"},
        "questions": {"coherent": {"type": "noul"}},
    }
    assert seen["timeout"] == 12.0
    assert result["answers"]["coherent"]["noul"] == 0.9


def test_decide_rejects_a_response_without_typed_answers(monkeypatch):
    monkeypatch.setattr(jev, "urlopen", lambda *_args, **_kwargs: Response({"model": "jev"}))

    with pytest.raises(jev.JevUnavailable, match="no typed answers"):
        asyncio.run(jev.decide(
            api_key="tsf-secret",
            model="jev-latest",
            state={},
            questions={"coherent": {"type": "noul"}},
        ))
