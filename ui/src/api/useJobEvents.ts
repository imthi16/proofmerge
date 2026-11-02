import { useEffect, useRef, useState } from "react";
import { ApiError, getJSON, paths } from "./client";
import type { Job, JobEvents, VerificationEvent } from "./types";
import { mergeEvents } from "../lib/pipeline";

export type Connection = "connecting" | "live" | "reconnecting" | "closed" | "unavailable";

export interface JobStream {
  job: Job | null;
  stages: string[];
  modelStages: string[];
  events: VerificationEvent[];
  connection: Connection;
  error: string;
  notFound: boolean;
}

const TERMINAL = new Set(["job.completed", "job.failed"]);
const EVENT_TYPES = [
  "job.created",
  "job.fetch_started",
  "job.fetch_completed",
  "stage.started",
  "stage.retrying",
  "stage.completed",
  "stage.skipped",
  "job.verdict_ready",
  "job.evidence_written",
  "job.completed",
  "job.failed",
];

type Source = Pick<EventSource, "addEventListener" | "close" | "readyState"> & {
  onopen: ((this: EventSource, ev: Event) => unknown) | null;
  onerror: ((this: EventSource, ev: Event) => unknown) | null;
};

const openEventSource = (url: string): Source => new EventSource(url);

/**
 * A job's snapshot, then its live events over SSE. EventSource reconnects by itself
 * and sends Last-Event-ID; mergeEvents drops anything already seen. When the stream
 * ends (the job finished) a final snapshot fills in the job's end state.
 */
const INITIAL: JobStream = {
  job: null,
  stages: [],
  modelStages: [],
  events: [],
  connection: "connecting",
  error: "",
  notFound: false,
};

export function useJobEvents(
  jobId: string,
  open: (url: string) => Source = openEventSource,
): JobStream {
  // State belongs to one job; a different jobId starts from INITIAL without a reset render.
  const [owned, setOwned] = useState<{ jobId: string; stream: JobStream }>({ jobId, stream: INITIAL });
  const lastId = useRef(0);
  useEffect(() => {
    let source: Source | null = null;
    let cancelled = false;
    lastId.current = 0;
    const setState = (change: (s: JobStream) => JobStream) =>
      setOwned((o) => ({ jobId, stream: change(o.jobId === jobId ? o.stream : INITIAL) }));

    const apply = (snapshot: JobEvents) => {
      setState((s) => {
        const events = mergeEvents(s.events, snapshot.events);
        lastId.current = events.at(-1)?.event_id ?? 0;
        return {
          ...s,
          job: snapshot.job,
          stages: snapshot.stages,
          modelStages: snapshot.model_stages,
          events,
        };
      });
    };

    const refresh = () =>
      getJSON<JobEvents>(paths.job(jobId))
        .then((snapshot) => !cancelled && apply(snapshot))
        .catch(() => undefined);

    getJSON<JobEvents>(paths.job(jobId))
      .then((snapshot) => {
        if (cancelled) return;
        apply(snapshot);
        const ended = snapshot.events.some((e) => TERMINAL.has(e.type));
        if (ended || snapshot.job.status !== "running") {
          setState((s) => ({ ...s, connection: "closed" }));
          return;
        }
        source = open(paths.jobEvents(jobId, snapshot.events.at(-1)?.event_id ?? 0));
        source.onopen = () => setState((s) => ({ ...s, connection: "live" }));
        source.onerror = () => {
          if (cancelled) return;
          // CLOSED means the server ended the stream or refused it; otherwise it retries.
          setState((s) => ({
            ...s,
            connection: source && source.readyState === 2 ? "closed" : "reconnecting",
          }));
          if (source && source.readyState === 2) void refresh();
        };
        for (const type of EVENT_TYPES) {
          source.addEventListener(type, (message: Event) => {
            const data = (message as MessageEvent<string>).data;
            let event: VerificationEvent;
            try {
              event = JSON.parse(data) as VerificationEvent;
            } catch {
              return;
            }
            setState((s) => {
              const events = mergeEvents(s.events, [event]);
              lastId.current = events.at(-1)?.event_id ?? 0;
              return { ...s, events, connection: "live" };
            });
            if (TERMINAL.has(event.type)) {
              source?.close();
              setState((s) => ({ ...s, connection: "closed" }));
              void refresh();
            }
          });
        }
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        const notFound = reason instanceof ApiError && reason.status === 404;
        setState((s) => ({
          ...s,
          connection: "unavailable",
          notFound,
          error: reason instanceof Error ? reason.message : String(reason),
        }));
      });
    return () => {
      cancelled = true;
      source?.close();
    };
  }, [jobId, open]);
  return owned.jobId === jobId ? owned.stream : INITIAL;
}
