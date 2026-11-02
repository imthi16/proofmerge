import type { Decision, DecisionCounts } from "../api/types";
import { DECISIONS } from "../api/types";

interface Props {
  counts: DecisionCounts;
  active?: string;
  onPick?: (decision: Decision | "") => void;
}

/** Recorded decisions, counted. Buttons when onPick is given (a filter), else a summary. */
export function DecisionBar({ counts, active = "", onPick }: Props) {
  const total = DECISIONS.reduce((sum, d) => sum + counts[d], 0);
  if (!total) return null;
  return (
    <div className={`dist${active ? " dist-filtered" : ""}`} aria-label="Recorded verdicts">
      {DECISIONS.filter((d) => counts[d]).map((d) => {
        const content = (
          <>
            <span className="dist-k">{d}</span>
            <span className="dist-n">{counts[d]}</span>
          </>
        );
        const style = { flexGrow: counts[d] };
        return onPick ? (
          <button
            key={d}
            type="button"
            className={`dist-seg dist-${d.toLowerCase()}`}
            style={style}
            aria-pressed={active === d}
            title={active === d ? "Show every verdict" : `Show only ${d}`}
            onClick={() => onPick(active === d ? "" : d)}
          >
            {content}
          </button>
        ) : (
          <div key={d} className={`dist-seg dist-${d.toLowerCase()}`} style={style}>
            {content}
          </div>
        );
      })}
    </div>
  );
}
