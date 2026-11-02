import { test } from "node:test";
import assert from "node:assert/strict";
import { reimbursableCents, CATEGORY_CAP_CENTS } from "./src/expenses/totals";

test("two same-category expenses at CAP sum per-expense caps", () => {
  const CAP = CATEGORY_CAP_CENTS.travel;
  const total = reimbursableCents([
    { id: "a", category: "travel", amountCents: CAP, status: "submitted" },
    { id: "b", category: "travel", amountCents: CAP, status: "submitted" },
  ]);
  assert.equal(total, 2 * CAP);
});
