import type { Decision } from "../api/types";
import { HEADLINE } from "../api/types";

// Each decision has a shape as well as a colour, so status never relies on colour alone.
const MARK: Record<Decision, string> = { BLOCK: "✕", REVIEW: "?", PASS: "✓" };

export function VerdictBadge({ decision, large = false }: { decision: Decision | null; large?: boolean }) {
  if (!decision) {
    return <span className="badge badge-error">ERROR</span>;
  }
  return (
    <span className={`badge badge-${decision.toLowerCase()}${large ? " badge-lg" : ""}`}>
      <span aria-hidden="true" className="badge-mark">
        {MARK[decision]}
      </span>
      {decision}
    </span>
  );
}

export function VerdictHeadline({ decision }: { decision: Decision }) {
  return <strong className="verdict-headline">{HEADLINE[decision]}</strong>;
}
