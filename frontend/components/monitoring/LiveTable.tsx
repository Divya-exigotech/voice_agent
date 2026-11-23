"use client";

import { useState, type ReactNode } from "react";

import { useLive } from "@/lib/useLive";
import type { Page } from "@/lib/types";

const PAGE_SIZE = 25;

export interface Column<T> {
  header: string;
  cell: (row: T) => ReactNode;
}

export function LiveTable<T>({
  label,
  columns,
  rowKey,
  load,
  filters,
  filterLabel = "Status",
}: {
  label: string;
  columns: Column<T>[];
  rowKey: (row: T) => string;
  load: (limit: number, offset: number, filter?: string) => Promise<Page<T>>;
  /** Optional filter values (e.g. statuses); an "All" option is added. */
  filters?: string[];
  filterLabel?: string;
}) {
  const [offset, setOffset] = useState(0);
  const [filter, setFilter] = useState("");
  const { data, error, updatedAt, refresh } = useLive(
    () => load(PAGE_SIZE, offset, filter || undefined),
    [offset, filter],
  );

  const total = data?.total ?? 0;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + PAGE_SIZE, total);

  return (
    <section className="card" aria-label={label}>
      <div className="live-toolbar">
        <p className="eyebrow">
          {label} · {total}
        </p>
        <span className="live-status muted small">
          <span className={`live-dot${error ? " live-dot-off" : ""}`} aria-hidden="true" />
          {error ? "Disconnected" : "Live"}
          {updatedAt && !error && ` · updated ${updatedAt.toLocaleTimeString("en-IN")}`}
        </span>
        {filters && (
          <label className="small live-filter">
            {filterLabel}{" "}
            <select
              value={filter}
              onChange={(e) => {
                setFilter(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">All</option>
              {filters.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </label>
        )}
        <button type="button" className="btn" onClick={refresh}>
          Refresh
        </button>
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!data && !error && <p className="muted small">Loading…</p>}
      {data && data.items.length === 0 && <p className="muted small">No records yet.</p>}
      {data && data.items.length > 0 && (
        <div className="live-scroll">
          <table className="table live-table">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c.header}>{c.header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.items.map((row) => (
                <tr key={rowKey(row)}>
                  {columns.map((c) => (
                    <td key={c.header}>{c.cell(row)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="live-pager">
        <span className="muted small">
          {from}–{to} of {total}
        </span>
        <button type="button" className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
          Previous
        </button>
        <button type="button" className="btn" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
          Next
        </button>
      </div>
    </section>
  );
}
