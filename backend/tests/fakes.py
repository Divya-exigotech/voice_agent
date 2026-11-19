import asyncio
from types import SimpleNamespace
from typing import Any


class _Recorder:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self._log = log

    async def create(self, **kwargs: Any) -> None:
        self._log.append(("create", kwargs))

    async def append(self, **kwargs: Any) -> None:
        self._log.append(("append", kwargs))


class QueueConnection:
    """Fake Foundry connection: events are fed through a queue, sends are recorded."""

    def __init__(self) -> None:
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.log: list[tuple[str, Any]] = []
        self.conversation = SimpleNamespace(item=_Recorder(self.log))
        self.response = _Recorder(self.log)
        self.input_audio_buffer = _Recorder(self.log)

    async def recv(self) -> dict[str, Any]:
        return await self.events.get()


class FakeSocket:
    """Fake browser WebSocket."""

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent_json: list[dict[str, Any]] = []
        self.sent_bytes: list[bytes] = []

    async def receive(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    async def send_json(self, data: Any) -> None:
        self.sent_json.append(data)
