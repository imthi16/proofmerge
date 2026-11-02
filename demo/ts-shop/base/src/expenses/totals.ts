export type Status = "draft" | "submitted" | "approved" | "rejected";
export type Category = "travel" | "meals" | "office";

export interface Expense {
  id: string;
  category: Category;
  amountCents: number;
  status: Status;
}

/** The most the company pays back for one expense in each category. */
export const CATEGORY_CAP_CENTS: Record<Category, number> = {
  travel: 50_000,
  meals: 7_500,
  office: 20_000,
};

/**
 * What the company pays back: submitted and approved expenses, each capped at its
 * category's limit. Draft and rejected expenses are never paid.
 */
export function reimbursableCents(expenses: Expense[]): number {
  let total = 0;
  for (const expense of expenses) {
    if (expense.status !== "submitted" && expense.status !== "approved") continue;
    if (expense.amountCents < 0) throw new RangeError(`negative amount on ${expense.id}`);
    total += Math.min(expense.amountCents, CATEGORY_CAP_CENTS[expense.category]);
  }
  return total;
}
