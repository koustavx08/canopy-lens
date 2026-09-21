/**
 * Driving one analysis: submit, poll, collect.
 *
 * The backend hands back a job id rather than a result, so this hook owns the
 * waiting. Everything it reports comes from the server — the phase is derived
 * from the job's own stage and the bar from the job's own tile count. Nothing
 * here advances on a timer, because a progress bar driven by elapsed time tells
 * the user how long they have waited, which they already know.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, cancelAnalysis, fetchJob, fetchResult, startAnalysis } from "./api";
import type {
  AnalysisPhase,
  AnalysisResult,
  DetectionSettings,
  JobStatus,
} from "../types";

const POLL_INTERVAL_MS = 1000;

/**
 * Backend stage -> the phase shown to the user.
 *
 * Unknown stages fall through to "processing" rather than throwing: a new
 * pipeline stage should not be able to break the progress display.
 */
const POST_PROCESSING_STAGES = new Set(["measuring", "summarising", "summarizing", "exporting"]);

export interface AnalysisState {
  phase: AnalysisPhase;
  jobId: string | null;
  job: JobStatus | null;
  result: AnalysisResult | null;
  error: ApiError | null;
  /** 0-1 while the file is being sent; null once the upload has finished. */
  uploadFraction: number | null;
}

const IDLE: AnalysisState = {
  phase: "idle",
  jobId: null,
  job: null,
  result: null,
  error: null,
  uploadFraction: null,
};

export function useAnalysis() {
  const [state, setState] = useState<AnalysisState>(IDLE);
  const runRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  // A run in flight when the component goes away must not keep polling.
  useEffect(() => () => abortRef.current?.abort(), []);

  /**
   * Stop the analysis, on the server as well as in the page.
   *
   * The server is told first and the page is released either way. A cancel
   * that could not be delivered leaves the browser exactly where the old
   * "stop watching" button left it, which is the worst case rather than a new
   * one; blocking the button on a failed request would leave the user watching
   * an analysis they have already said they do not want.
   */
  const cancel = useCallback(async () => {
    const jobId = state.jobId;
    if (jobId) {
      try {
        await cancelAnalysis(jobId);
      } catch {
        /* nothing useful to do: the page is being released regardless */
      }
    }
    runRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    setState(IDLE);
  }, [state.jobId]);

  const reset = useCallback(() => {
    runRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    setState(IDLE);
  }, []);

  const run = useCallback(
    async (image: File, boundary: File | null, settings: DetectionSettings) => {
      runRef.current += 1;
      const token = runRef.current;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // Stale runs must not write over a newer one's state.
      const current = () => runRef.current === token && !controller.signal.aborted;
      const put = (patch: Partial<AnalysisState>) => {
        if (current()) setState((previous) => ({ ...previous, ...patch }));
      };

      setState({ ...IDLE, phase: "uploading", uploadFraction: 0 });

      try {
        const accepted = await startAnalysis(image, boundary, settings, {
          signal: controller.signal,
          onUploadProgress: (fraction) => put({ uploadFraction: fraction }),
        });
        if (!current()) return;
        put({ jobId: accepted.job_id, phase: "processing", uploadFraction: null });

        const job = await poll(accepted.job_id, controller.signal, current, (update) =>
          put({ job: update, phase: phaseFor(update) }),
        );
        if (!current()) return;

        if (job.status === "cancelled") {
          // Somebody asked for this. There is nothing to report and nothing
          // to show, so the page goes back to where it started.
          setState(IDLE);
          return;
        }

        if (job.status === "failed") {
          // The job carries the failure's own code and message; asking for the
          // result re-raises it with the status a synchronous call would have
          // returned, which is the version worth showing.
          throw job.error
            ? new ApiError(job.error, 500)
            : new ApiError(
                { code: "analysis_failed", message: "The analysis did not complete.", detail: {} },
                500,
              );
        }

        const result = await fetchResult(accepted.job_id, controller.signal);
        put({ result, phase: "complete", job });
      } catch (error) {
        if (!current()) return;
        if (error instanceof DOMException && error.name === "AbortError") return;
        put({
          phase: "error",
          uploadFraction: null,
          error:
            error instanceof ApiError
              ? error
              : new ApiError(
                  {
                    code: "unexpected_error",
                    message: "The analysis could not be completed.",
                    detail: { cause: String(error) },
                  },
                  0,
                ),
        });
      }
    },
    [],
  );

  return { ...state, run, reset, cancel };
}

/** Poll until the job finishes, reporting every update on the way. */
async function poll(
  jobId: string,
  signal: AbortSignal,
  current: () => boolean,
  onUpdate: (job: JobStatus) => void,
): Promise<JobStatus> {
  for (;;) {
    const job = await fetchJob(jobId, signal);
    if (!current()) return job;
    onUpdate(job);
    if (job.status === "completed" || job.status === "failed") return job;
    // A job cancelled elsewhere - another tab, or a restart that closed it
    // out - is finished too, and polling it forever would never end.
    if (job.status === "cancelled") return job;
    await sleep(POLL_INTERVAL_MS, signal);
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

function phaseFor(job: JobStatus): AnalysisPhase {
  if (job.status === "cancelled") return "idle";
  if (job.status === "failed") return "error";
  if (job.status === "completed") return "post-processing";
  return POST_PROCESSING_STAGES.has(job.stage) ? "post-processing" : "processing";
}
