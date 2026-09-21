/**
 * The hook that owns the waiting.
 *
 * The backend hands back a job id rather than a result, so everything between
 * "run" and "here are your crowns" lives here: the upload, the polling, what
 * happens when a job fails, and what happens when the user gives up on it.
 * The API client is mocked because none of that is about HTTP - it is about
 * which state the interface is in while the server takes its time.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { useAnalysis } from "./useAnalysis";
import { job, result } from "../test/fixtures";
import type { DetectionSettings } from "../types";

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    startAnalysis: vi.fn(),
    fetchJob: vi.fn(),
    fetchResult: vi.fn(),
    cancelAnalysis: vi.fn(),
  };
});

const api = await import("./api");

const SETTINGS: DetectionSettings = {
  confidenceThreshold: 0.3,
  minCrownAreaM2: 1,
  engine: "classical",
};

const IMAGE = new File(["raster"], "scene.tif");

beforeEach(() => {
  vi.mocked(api.startAnalysis).mockResolvedValue({
    job_id: "job-1",
    status: "queued",
    status_url: "/api/jobs/job-1",
    result_url: "/api/results/job-1",
  });
  vi.mocked(api.cancelAnalysis).mockResolvedValue(job({ status: "cancelled" }));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("a run that succeeds", () => {
  it("ends holding the result the server measured", async () => {
    vi.mocked(api.fetchJob).mockResolvedValue(job({ status: "completed", progress: 1 }));
    vi.mocked(api.fetchResult).mockResolvedValue(result());

    const { result: hook } = renderHook(() => useAnalysis());
    await act(async () => {
      await hook.current.run(IMAGE, null, SETTINGS);
    });

    expect(hook.current.phase).toBe("complete");
    expect(hook.current.result?.summary.tree_count).toBe(5);
  });

  it("reports upload progress while the file is being sent", async () => {
    vi.mocked(api.fetchJob).mockResolvedValue(job({ status: "completed" }));
    vi.mocked(api.fetchResult).mockResolvedValue(result());
    const seen: (number | null)[] = [];
    vi.mocked(api.startAnalysis).mockImplementation(async (_i, _b, _s, options) => {
      options?.onUploadProgress?.(0.5);
      return {
        job_id: "job-1",
        status: "queued",
        status_url: "/api/jobs/job-1",
        result_url: "/api/results/job-1",
      };
    });

    const { result: hook } = renderHook(() => useAnalysis());
    await act(async () => {
      await hook.current.run(IMAGE, null, SETTINGS);
    });
    seen.push(hook.current.uploadFraction);

    // Null once the upload is done: a bar stuck at 50% through a five-minute
    // analysis says the upload is still going, which it is not.
    expect(seen).toEqual([null]);
  });
});

describe("a run that fails", () => {
  it("shows the failure the server described, not a generic one", async () => {
    vi.mocked(api.fetchJob).mockResolvedValue(
      job({
        status: "failed",
        stage: "failed",
        error: {
          code: "missing_georeference",
          message: "This imagery carries no coordinate system.",
          detail: {},
        },
      }),
    );

    const { result: hook } = renderHook(() => useAnalysis());
    await act(async () => {
      await hook.current.run(IMAGE, null, SETTINGS);
    });

    expect(hook.current.phase).toBe("error");
    expect(hook.current.error?.code).toBe("missing_georeference");
  });

  it("still has something to say when the job failed without saying why", async () => {
    vi.mocked(api.fetchJob).mockResolvedValue(job({ status: "failed", error: null }));

    const { result: hook } = renderHook(() => useAnalysis());
    await act(async () => {
      await hook.current.run(IMAGE, null, SETTINGS);
    });

    expect(hook.current.phase).toBe("error");
    expect(hook.current.error?.message).toBeTruthy();
  });

  it("does not report an upload the user aborted as an error", async () => {
    vi.mocked(api.startAnalysis).mockRejectedValue(new DOMException("Aborted", "AbortError"));

    const { result: hook } = renderHook(() => useAnalysis());
    await act(async () => {
      await hook.current.run(IMAGE, null, SETTINGS);
    });

    expect(hook.current.phase).not.toBe("error");
  });

  it("wraps something that is not an ApiError rather than leaking it", async () => {
    vi.mocked(api.fetchJob).mockRejectedValue(new TypeError("undefined is not a function"));

    const { result: hook } = renderHook(() => useAnalysis());
    await act(async () => {
      await hook.current.run(IMAGE, null, SETTINGS);
    });

    expect(hook.current.error).toBeInstanceOf(ApiError);
    expect(hook.current.error?.code).toBe("unexpected_error");
  });
});

describe("cancelling", () => {
  it("tells the server to stop, not just the page to look away", async () => {
    vi.mocked(api.fetchJob).mockResolvedValue(job({ status: "running" }));

    const { result: hook } = renderHook(() => useAnalysis());
    act(() => {
      void hook.current.run(IMAGE, null, SETTINGS);
    });
    await waitFor(() => expect(hook.current.jobId).toBe("job-1"));

    await act(async () => {
      await hook.current.cancel();
    });

    expect(api.cancelAnalysis).toHaveBeenCalledWith("job-1");
    expect(hook.current.phase).toBe("idle");
  });

  it("releases the page even when the request could not be delivered", async () => {
    // Refusing to release it would leave the user watching an analysis they
    // have already said they do not want.
    vi.mocked(api.fetchJob).mockResolvedValue(job({ status: "running" }));
    vi.mocked(api.cancelAnalysis).mockRejectedValue(new TypeError("Failed to fetch"));

    const { result: hook } = renderHook(() => useAnalysis());
    act(() => {
      void hook.current.run(IMAGE, null, SETTINGS);
    });
    await waitFor(() => expect(hook.current.jobId).toBe("job-1"));

    await act(async () => {
      await hook.current.cancel();
    });

    expect(hook.current.phase).toBe("idle");
  });

  it("asks for nothing when there is no job yet", async () => {
    const { result: hook } = renderHook(() => useAnalysis());

    await act(async () => {
      await hook.current.cancel();
    });

    expect(api.cancelAnalysis).not.toHaveBeenCalled();
  });

  it("stops watching a job cancelled somewhere else", async () => {
    // Another tab, or a restart that closed the job out. Polling it forever
    // would never end.
    vi.mocked(api.fetchJob).mockResolvedValue(job({ status: "cancelled" }));

    const { result: hook } = renderHook(() => useAnalysis());
    await act(async () => {
      await hook.current.run(IMAGE, null, SETTINGS);
    });

    expect(hook.current.phase).toBe("idle");
    expect(api.fetchResult).not.toHaveBeenCalled();
  });
});

describe("reset", () => {
  it("clears a finished run without telling the server anything", async () => {
    vi.mocked(api.fetchJob).mockResolvedValue(job({ status: "completed" }));
    vi.mocked(api.fetchResult).mockResolvedValue(result());

    const { result: hook } = renderHook(() => useAnalysis());
    await act(async () => {
      await hook.current.run(IMAGE, null, SETTINGS);
    });
    act(() => hook.current.reset());

    expect(hook.current.phase).toBe("idle");
    expect(hook.current.result).toBeNull();
    expect(api.cancelAnalysis).not.toHaveBeenCalled();
  });
});
