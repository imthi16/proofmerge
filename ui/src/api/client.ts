import type { Job, Session } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function read<T>(response: Response): Promise<T> {
  const data = (await response.json().catch(() => ({}))) as { error?: string };
  if (!response.ok) throw new ApiError(data.error || `HTTP ${response.status}`, response.status);
  return data as T;
}

export async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  return read<T>(await fetch(path, { cache: "no-store", signal }));
}

/** Start a verification. The server checks the session token, Origin and Host. */
export async function startVerification(session: Session, number: number): Promise<Job> {
  const response = await fetch("/api/v1/verifications", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-ProofMerge-Token": session.token },
    body: JSON.stringify({ number }),
  });
  return read<Job>(response);
}

export const paths = {
  session: "/api/v1/session",
  overview: "/api/v1/overview",
  runs: (query: URLSearchParams) => `/api/v1/runs?${query.toString()}`,
  run: (id: string) => `/api/v1/runs/${encodeURIComponent(id)}`,
  report: (id: string) => `/api/v1/runs/${encodeURIComponent(id)}/report`,
  benchmarks: "/api/v1/benchmarks",
  pullRequests: (refresh = false) => `/api/v1/pull-requests${refresh ? "?refresh=1" : ""}`,
  job: (id: string) => `/api/v1/jobs/${encodeURIComponent(id)}`,
  jobEvents: (id: string, last: number) =>
    `/api/v1/jobs/${encodeURIComponent(id)}/events?last_event_id=${last}`,
  activeJob: "/api/v1/jobs/active",
  diagnostics: "/api/v1/diagnostics",
};
