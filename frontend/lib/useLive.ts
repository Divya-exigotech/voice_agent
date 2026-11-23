"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export const LIVE_INTERVAL_MS = 3000;

export interface Live<T> {
  data: T | null;
  error: string | null;
  updatedAt: Date | null;
  refresh: () => void;
}

/**
 * Loads `load()` now and again every few seconds while the tab is visible, so
 * pages follow the database as calls start and end. A failed refresh keeps the
 * last good data on screen and reports the error.
 */
export function useLive<T>(load: () => Promise<T>, deps: unknown[], intervalMs = LIVE_INTERVAL_MS): Live<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);

  useEffect(() => {
    loadRef.current = load;
  });

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function run() {
      if (typeof document !== "undefined" && document.hidden) {
        timer = setTimeout(run, intervalMs);
        return;
      }
      try {
        const result = await loadRef.current();
        if (cancelled) return;
        setData(result);
        setError(null);
        setUpdatedAt(new Date());
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      }
      if (!cancelled) timer = setTimeout(run, intervalMs);
    }

    void run();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, intervalMs]);

  const refresh = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, updatedAt, refresh };
}
