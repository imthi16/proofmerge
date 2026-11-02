import { test } from "node:test";
import assert from "node:assert/strict";
import { reimbursableCents } from "./src/expenses/totals";

test("draft expense is skipped", () => {
  const total = reimbursableCents([{ id: "a", category: "travel", amountCents: 1, status: "draft" }]);
  assert.equal(total, 0);
});
