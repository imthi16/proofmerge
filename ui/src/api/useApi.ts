import { useCallback, useEffect, useState } from "react";
import { getJSON } from "./client";

export interface ApiState<T> {
  data: T | null;
  error: string;
  loading: boolean;
  reload: () => void;
}

interface Result<T> {
  key: string | null;
  data: T | null;
  error: string;
}

/**
 * GET a URL; refetch when it changes or on reload(). The last data stays visible while
 * a newer request is in flight, and stale responses are discarded.
 */
export function useApi<T>(url: string | null): ApiState<T> {
  const [tick, setTick] = useState(0);
  const key = url ? `${tick}:${url}` : null;
  const [result, setResult] = useState<Result<T>>({ key: null, data: null, error: "" });
  useEffect(() => {
    if (!url || !key) return;
    const controller = new AbortController();
    getJSON<T>(url, controller.signal)
      .then((data) => setResult({ key, data, error: "" }))
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        const error = reason instanceof Error ? reason.message : String(reason);
        setResult((previous) => ({ key, data: previous.data, error }));
      });
    return () => controller.abort();
  }, [url, key]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return {
    data: result.data,
    error: result.key === key ? result.error : "",
    loading: key !== null && result.key !== key,
    reload,
  };
}
