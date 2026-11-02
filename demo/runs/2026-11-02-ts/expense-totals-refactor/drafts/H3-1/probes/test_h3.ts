import { test } from "node:test";
import assert from "node:assert/strict";
import { reimbursableCents } from "./src/expenses/totals";

test("negative draft is ignored with no throw", () => {
  let result: number | undefined;
  try {
    result = reimbursableCents([{ id: "d1", category: "travel", amountCents: -5, status: "draft" }]);
  } catch (e) {
    assert.fail(`expected no throw for negative draft, got ${e}`);
  }
  assert.equal(result, 0);
});
