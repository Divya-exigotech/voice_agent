from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TranscriptTurn(BaseModel):
    turn: int
    speaker: Literal["agent", "user"]
    text: str
    timestamp: datetime
