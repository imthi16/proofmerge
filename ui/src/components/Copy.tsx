import { useEffect, useState } from "react";

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const timer = window.setTimeout(() => setState("idle"), 1400);
    return () => window.clearTimeout(timer);
  }, [state]);
  const copy = () => {
    if (!navigator.clipboard) {
      setState("failed");
      return;
    }
    navigator.clipboard.writeText(text).then(
      () => setState("done"),
      () => setState("failed"),
    );
  };
  return (
    <button type="button" className="btn btn-small" onClick={copy} aria-label={`Copy ${label}`}>
      {state === "done" ? "Copied" : state === "failed" ? "Copy failed" : "Copy"}
    </button>
  );
}
