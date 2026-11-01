import { describe, expect, it } from "vitest";
import type { RunPage, RunSummary } from "../src/api/types";
import { allMatchingRuns } from "../src/pages/RunsPage";

const row = (i: number) => ({ id: `run-${i}` }) as RunSummary;

function server(total: number, shrinkAfter?: number) {
  const seen: string[] = [];
  const get = async (url: string): Promise<RunPage> => {
    seen.push(url);
    const q = new URLSearchParams(url.split("?")[1]);
    const offset = Number(q.get("offset"));
    const limit = Number(q.get("limit"));
    const now = shrinkAfter !== undefined && seen.length > 1 ? shrinkAfter : total;
    const runs = Array.from({ length: Math.max(0, Math.min(limit, now - offset)) }, (_, i) => row(offset + i));
    return { root: "r", total: now, offset, limit, runs, run_sets: [],
      counts: { PASS: 0, REVIEW: 0, BLOCK: 0, unreadable: 0 } };
  };
  return { get, seen };
}

describe("allMatchingRuns", () => {
  it("fetches every page past the 500-row limit, keeping the filters", async () => {
    const { get, seen } = server(1234);
    const { rows } = await allMatchingRuns(new URLSearchParams({ decision: "BLOCK", offset: "50" }), get);
    expect(rows).toHaveLength(1234);
    expect(new Set(rows.map((r) => r.id)).size).toBe(1234);
    expect(seen).toHaveLength(3);
    expect(seen.every((u) => u.includes("decision=BLOCK") && u.includes("limit=500"))).toBe(true);
    expect(seen[0]).toContain("offset=0");
  });

  it("refuses a partial export when the list changes underneath", async () => {
    const { get } = server(900, 700);
    await expect(allMatchingRuns(new URLSearchParams(), get)).rejects.toThrow(/changed during export/);
  });
});
