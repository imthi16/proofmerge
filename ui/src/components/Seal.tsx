import type { RunSummary } from "../api/types";

const TEXT = { intact: "evidence intact", changed: "evidence changed", unsealed: "no manifest" };
const MARK = { intact: "●", changed: "▲", unsealed: "○" };

export function Seal({ integrity }: { integrity: RunSummary["integrity"] }) {
  if (!integrity) return null;
  return (
    <span className={`seal seal-${integrity.status}`}>
      <span aria-hidden="true">{MARK[integrity.status]}</span> {TEXT[integrity.status]}
    </span>
  );
}
