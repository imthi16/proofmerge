import type { ReactNode } from "react";

export function Loading({ what }: { what: string }) {
  return (
    <p className="status-line" role="status">
      <span className="spinner" aria-hidden="true" /> Loading {what}…
    </p>
  );
}

export function ErrorBox({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  return (
    <div className="error-box" role="alert">
      <div>{children}</div>
      {onRetry ? (
        <button type="button" className="btn" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children ? <div>{children}</div> : null}
    </div>
  );
}
