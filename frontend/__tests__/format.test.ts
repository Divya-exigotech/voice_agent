import { describe, expect, it } from "vitest";

import { formatDate, formatDuration, humanize } from "@/lib/format";

describe("format helpers", () => {
  it("formats durations", () => {
    expect(formatDuration(0)).toBe("00:00");
    expect(formatDuration(83_000)).toBe("01:23");
    expect(formatDuration(3_723_000)).toBe("1:02:03");
  });

  it("formats ISO dates without shifting the day", () => {
    expect(formatDate("2026-11-20")).toBe("20 Nov 2026");
  });

  it("humanizes outcome ids", () => {
    expect(humanize("completed_no_commitment")).toBe("Completed no commitment");
  });
});
