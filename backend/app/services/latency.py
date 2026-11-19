import statistics
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LatencySummary:
    count: int
    p50: float
    p95: float
    max: float


def summarize(values: list[float]) -> LatencySummary | None:
    """P50/P95/max of latency samples in seconds; None when there are no samples."""
    if not values:
        return None
    if len(values) == 1:
        only = values[0]
        return LatencySummary(1, only, only, only)
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return LatencySummary(len(values), statistics.median(values), cuts[94], max(values))
