import { describe, expect, it } from "vitest";
import { reimbursableCents } from "./totals";

describe("reimbursableCents", () => {
  it("pays an approved expense in full under its cap", () => {
    expect(reimbursableCents([{ id: "e1", category: "travel", amountCents: 1_200, status: "approved" }])).toBe(1_200);
  });

  it("never pays a rejected expense", () => {
    expect(reimbursableCents([{ id: "e2", category: "office", amountCents: 900, status: "rejected" }])).toBe(0);
  });
});
