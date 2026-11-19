"use client";

import { useEffect, useState } from "react";

import { formatDuration, formatTime } from "@/lib/format";

/** "Started 10:02:13 · 01:23" while a session is running. */
export function SessionTimer({ startedAt }: { startedAt: string }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const elapsed = Math.max(0, now - new Date(startedAt).getTime());
  return (
    <p className="muted small session-timer" data-testid="session-timer">
      Started {formatTime(startedAt)} · <span aria-label="elapsed">{formatDuration(elapsed)}</span>
    </p>
  );
}
