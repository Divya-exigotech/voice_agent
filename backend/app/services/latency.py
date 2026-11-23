"""In-process latency recorder behind the dashboard's Latency page.

The app already measures these timings and writes them to the log; this keeps
the most recent samples in memory too, so the dashboard can show them. It is
cheap and thread-safe (database work runs in worker threads). History starts
empty when the backend restarts.
"""

from __future__ import annotations

import itertools
import math
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime

MAX_SAMPLES = 5000

# Metric names, in display order.
VOICE_REPLY = "voice_reply"
DB_SESSION_START = "db_session_start"
DB_LIVE_WRITE = "db_live_write"
DB_FINAL_SAVE = "db_final_save"
HTTP = "http"
METRICS = (VOICE_REPLY, DB_SESSION_START, DB_LIVE_WRITE, DB_FINAL_SAVE, HTTP)


@dataclass(frozen=True)
class LatencySample:
    seq: int
    metric: str
    milliseconds: float
    at: datetime
    label: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class LatencyStats:
    metric: str
    count: int
    average_ms: float | None
    min_ms: float | None
    max_ms: float | None
    p50_ms: float | None
    p95_ms: float | None
    last_ms: float | None
    last_at: datetime | None


def _percentile(sorted_values: list[float], fraction: float) -> float:
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


class LatencyRecorder:
    def __init__(self, max_samples: int = MAX_SAMPLES) -> None:
        self._samples: deque[LatencySample] = deque(maxlen=max_samples)
        self._lock = threading.Lock()
        self._seq = itertools.count(1)

    def record(
        self,
        metric: str,
        milliseconds: float,
        *,
        label: str | None = None,
        session_id: str | None = None,
    ) -> None:
        with self._lock:
            self._samples.append(
                LatencySample(
                    seq=next(self._seq),
                    metric=metric,
                    milliseconds=milliseconds,
                    at=datetime.now(UTC),
                    label=label,
                    session_id=session_id,
                )
            )

    def _snapshot(self) -> list[LatencySample]:
        with self._lock:
            return list(self._samples)

    def stats(self) -> list[LatencyStats]:
        samples = self._snapshot()
        result: list[LatencyStats] = []
        for metric in METRICS:
            mine = [s for s in samples if s.metric == metric]
            if not mine:
                result.append(
                    LatencyStats(metric, 0, None, None, None, None, None, None, None)
                )
                continue
            values = sorted(s.milliseconds for s in mine)
            result.append(
                LatencyStats(
                    metric=metric,
                    count=len(values),
                    average_ms=sum(values) / len(values),
                    min_ms=values[0],
                    max_ms=values[-1],
                    p50_ms=_percentile(values, 0.5),
                    p95_ms=_percentile(values, 0.95),
                    last_ms=mine[-1].milliseconds,
                    last_at=mine[-1].at,
                )
            )
        return result

    def recent(
        self, *, limit: int, offset: int, metric: str | None
    ) -> tuple[int, list[LatencySample]]:
        """Newest first; returns the total matching count and one page."""
        samples = [
            s
            for s in reversed(self._snapshot())
            if metric is None or s.metric == metric
        ]
        return len(samples), samples[offset : offset + limit]


# One recorder for the whole process.
recorder = LatencyRecorder()
