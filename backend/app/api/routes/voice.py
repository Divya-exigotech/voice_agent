"""Browser voice WebSocket: microphone audio in, agent audio + events out.

Protocol (`/api/sessions/{session_id}/voice`):
  browser -> server  binary : PCM16 little-endian, mono, 24 kHz microphone audio
                     text   : {"type": "stop"} to hang up the browser side
  server -> browser  binary : PCM16 little-endian, mono, 24 kHz agent audio
                     text   : {"type": "ready" | "transcript", "transcript": [...], "proposed_outcome": ...}
                              {"type": "speech_started" | "speech_stopped" | "response_done"}
                              {"type": "session_ended"} / {"type": "agent_closed", "reason": ...}
                              {"type": "error", "message": ...}
The live agent session outlives the socket; End Session (REST finalize) closes it.
"""

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.conversation import AgentNotConfiguredError, ConversationService
from app.services.session_manager import SessionNotActiveError, SessionNotFoundError
from app.services.voice_agent import AgentError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice"])

# 1 s of PCM16 at 24 kHz is 48 000 bytes; browser chunks are ~100 ms.
MAX_AUDIO_FRAME_BYTES = 96_000

CLOSE_FORBIDDEN_ORIGIN = 4403
CLOSE_NOT_FOUND = 4404
CLOSE_NOT_ACTIVE = 4409
CLOSE_UNAVAILABLE = 4503
CLOSE_AGENT_ERROR = 4502

_END = object()


async def _send_to_browser(websocket: WebSocket, outbox: "asyncio.Queue[Any]") -> None:
    while True:
        item = await outbox.get()
        if item is _END:
            return
        try:
            if isinstance(item, (bytes, bytearray)):
                await websocket.send_bytes(bytes(item))
            else:
                await websocket.send_text(json.dumps(item))
        except Exception:  # noqa: BLE001 - any send failure means the browser went away
            return  # browser went away; the receive loop will notice


@router.websocket("/sessions/{session_id}/voice")
async def voice_session(websocket: WebSocket, session_id: str) -> None:
    settings = websocket.app.state.settings
    service: ConversationService = websocket.app.state.conversation_service

    # Browsers always send Origin; reject cross-site pages (CSWSH).
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in settings.allowed_origins:
        logger.warning("Rejected voice WebSocket from origin %s", origin)
        await websocket.close(code=CLOSE_FORBIDDEN_ORIGIN)
        return

    await websocket.accept()

    async def fail(code: int, message: str) -> None:
        await websocket.send_text(json.dumps({"type": "error", "message": message}))
        await websocket.close(code=code)

    outbox: asyncio.Queue[Any] = asyncio.Queue()

    async def sink(event: Any) -> None:
        outbox.put_nowait(event)

    try:
        await service.attach_voice(session_id, sink)
    except SessionNotFoundError:
        return await fail(CLOSE_NOT_FOUND, "Session not found")
    except SessionNotActiveError:
        return await fail(CLOSE_NOT_ACTIVE, "Session has already ended")
    except AgentNotConfiguredError as exc:
        return await fail(CLOSE_UNAVAILABLE, str(exc))
    except AgentError as exc:
        return await fail(CLOSE_AGENT_ERROR, f"Voice agent error: {exc}")

    sender = asyncio.create_task(_send_to_browser(websocket, outbox))
    close_code = 1000
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                if 0 < len(data) <= MAX_AUDIO_FRAME_BYTES and len(data) % 2 == 0:
                    await service.append_audio(session_id, data)
                continue
            text = message.get("text")
            if text:
                try:
                    command = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(command, dict) and command.get("type") == "stop":
                    break
    except WebSocketDisconnect:
        pass
    except AgentError as exc:
        outbox.put_nowait({"type": "error", "message": f"Voice agent error: {exc}"})
        close_code = CLOSE_AGENT_ERROR
    finally:
        await service.detach_voice(session_id, sink)
        outbox.put_nowait(_END)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(sender, timeout=2)
        with contextlib.suppress(Exception):
            await websocket.close(code=close_code)
