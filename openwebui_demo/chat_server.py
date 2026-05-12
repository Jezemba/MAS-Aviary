"""OpenAI-compatible chat-completions server that streams MAS-Aviary
pipeline events as a single assistant message.

Open WebUI talks to this server as if it were an OpenAI endpoint. Two
"models" are exposed:

    mas-aviary-live    — spawn ./run_batch.sh and stream the real run
    mas-aviary-replay  — replay the cached run #6 transcript

Selection happens via the ``model`` field of the chat-completions
request (Open WebUI surfaces it as a model picker). The user-supplied
prompt is acknowledged but does NOT change the task — per design, the
F25 spec is baked in.

Endpoints:
    GET  /v1/models                    — model catalogue
    POST /v1/chat/completions          — streaming or non-streaming
    GET  /healthz                      — liveness

Run:
    /home/aipexws3/Jessica/Avion/.venv/bin/python chat_server.py
    # listens on http://127.0.0.1:8090 by default
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from event_parser import Event
from formatter import FormatterState, format_event
from live_runner import live
from replay_runner import replay

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("mas_aviary_chat")

app = FastAPI(title="MAS-Aviary Chat Demo", version="0.1.0")

# Permissive CORS so Open WebUI (running in Docker) can hit the host.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


MODELS = {
    "mas-aviary-live": {
        "id": "mas-aviary-live",
        "object": "model",
        "owned_by": "mas-aviary",
        "description": (
            "Live run: spawns the real 5-MCP pipeline with Claude Sonnet 4. "
            "~10 minutes per run, costs Anthropic API tokens."
        ),
    },
    "mas-aviary-replay": {
        "id": "mas-aviary-replay",
        "object": "model",
        "owned_by": "mas-aviary",
        "description": (
            "Replay: streams cached Run #6 transcript at ~10x realtime. "
            "Free, deterministic, ~30 seconds end-to-end."
        ),
    },
}


def _event_stream_from_model(model_id: str) -> AsyncIterator[Event]:
    """Return the right async iterator based on the chosen model."""
    if model_id == "mas-aviary-live":
        return live()
    return replay(speed=10.0)


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


def _delta_chunk(content: str, model_id: str, completion_id: str) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {"index": 0, "delta": {"content": content}, "finish_reason": None}
        ],
    }


def _final_chunk(model_id: str, completion_id: str) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }


async def _stream_events(model_id: str, completion_id: str) -> AsyncIterator[bytes]:
    """Drive the event source and emit OpenAI-style SSE deltas."""
    state = FormatterState()
    # The "role" delta is required by some clients (sets the message
    # role on the assistant message).
    first_role = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
        ],
    }
    yield _sse(first_role)

    try:
        async for ev in _event_stream_from_model(model_id):
            chunk = format_event(ev, state)
            if not chunk:
                continue
            yield _sse(_delta_chunk(chunk, model_id, completion_id))
    except asyncio.CancelledError:
        logger.info("Client disconnected; cancelling event stream.")
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("Event stream error")
        yield _sse(_delta_chunk(
            f"\n\n> ⚠️ Stream error: {type(e).__name__}: {e}\n",
            model_id, completion_id,
        ))
    finally:
        yield _sse(_final_chunk(model_id, completion_id))
        yield b"data: [DONE]\n\n"


async def _full_completion(model_id: str, completion_id: str) -> dict:
    """Non-streaming response — collect everything then return as one
    chat.completion. Used when the client requests stream=False."""
    state = FormatterState()
    chunks: list[str] = []
    try:
        async for ev in _event_stream_from_model(model_id):
            piece = format_event(ev, state)
            if piece:
                chunks.append(piece)
    except Exception as e:  # noqa: BLE001
        chunks.append(f"\n\n> ⚠️ {type(e).__name__}: {e}\n")
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "".join(chunks),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/v1/models")
async def list_models() -> dict:
    return {"object": "list", "data": list(MODELS.values())}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    model_id = body.get("model") or "mas-aviary-replay"
    stream = bool(body.get("stream", False))
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    if model_id not in MODELS:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": f"Unknown model '{model_id}'. Available: "
                               f"{list(MODELS)}",
                    "type": "invalid_request_error",
                }
            },
        )

    if stream:
        return StreamingResponse(
            _stream_events(model_id, completion_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return JSONResponse(await _full_completion(model_id, completion_id))


def main():
    import uvicorn
    host = "0.0.0.0"
    port = 8090
    logger.info("Starting MAS-Aviary chat server on http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
