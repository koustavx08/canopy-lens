/**
 * The API client, and what it does with a response it did not want.
 *
 * The backend answers every failure with `{error: {code, message, detail}}`,
 * phrased for the person reading it. The client's job is to carry that through
 * intact: an interface that replaces "this imagery has no coordinate system"
 * with "request failed" has thrown away the only part the user could act on.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiUrl, cancelAnalysis, fetchJob, previewBoundary, request } from "./api";
import { job } from "../test/fixtures";

/**
 * The error a call produced, typed.
 *
 * `catch` hands back `unknown`, which is correct and unusable: every
 * assertion below is about a field of the error, and asserting on `unknown`
 * does not compile. Failing loudly when the call *succeeds* matters too - a
 * test named for a failure that silently passed on a 200 tests nothing.
 */
async function failureOf(call: Promise<unknown>): Promise<ApiError> {
  return call.then(
    () => {
      throw new Error("expected the call to fail, but it succeeded");
    },
    (error: unknown) => error as ApiError,
  );
}

function respondWith(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("request", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the parsed body when the call succeeds", async () => {
    vi.mocked(fetch).mockResolvedValue(respondWith({ ok: true }));

    await expect(request("/api/thing")).resolves.toEqual({ ok: true });
  });

  it("keeps the server's own code and message", async () => {
    vi.mocked(fetch).mockResolvedValue(
      respondWith(
        {
          error: {
            code: "missing_georeference",
            message: "This imagery carries no coordinate system.",
            detail: { filename: "scene.png" },
          },
        },
        422,
      ),
    );

    const failure = await failureOf(request("/api/analyze"));

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.code).toBe("missing_georeference");
    expect(failure.message).toBe("This imagery carries no coordinate system.");
    expect(failure.detail).toEqual({ filename: "scene.png" });
    expect(failure.status).toBe(422);
  });

  it("falls back to the status when the body is not the expected envelope", async () => {
    // A proxy timing out, or nginx answering before the app did. There is no
    // server explanation to show, so the status is what there is.
    vi.mocked(fetch).mockResolvedValue(
      new Response("<html>502 Bad Gateway</html>", { status: 502 }),
    );

    const failure = await failureOf(request("/api/thing"));

    expect(failure.code).toBe("http_502");
    expect(failure.status).toBe(502);
  });

  it("turns an unreachable backend into an error with a code, not a raw throw", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("Failed to fetch"));

    const failure = await failureOf(request("/api/thing"));

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.code).toBe("network_unreachable");
    expect(failure.status).toBe(0);
  });

  it("passes the caller's own headers through alongside its own", async () => {
    vi.mocked(fetch).mockResolvedValue(respondWith({}));

    await request("/api/thing", { headers: { "X-Test": "yes" } });

    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect((init?.headers as Record<string, string>)["X-Test"]).toBe("yes");
  });
});

describe("the endpoints", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respondWith(job())));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("asks for a job by id", async () => {
    await fetchJob("job-1");

    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/jobs/job-1");
  });

  it("escapes an id rather than pasting it into a path", async () => {
    await fetchJob("../../etc/passwd");

    expect(vi.mocked(fetch).mock.calls[0][0]).not.toContain("../");
  });

  it("cancels with a POST, because it changes something", async () => {
    await cancelAnalysis("job-1");

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("/api/jobs/job-1/cancel");
    expect(init?.method).toBe("POST");
  });

  it("sends a boundary as the field the endpoint actually reads", async () => {
    // The field is `file`. Sending `boundary` gets a 422 naming a field the
    // user never saw, which is a long way to travel for a typo.
    await previewBoundary(new File(["{}"], "site.geojson"));

    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect((init?.body as FormData).get("file")).toBeInstanceOf(File);
  });
});

describe("apiUrl", () => {
  it("is a bare path when the API is on this origin", () => {
    // Which is the case in development behind the Vite proxy, and in the
    // Docker stack behind nginx.
    expect(apiUrl("/api/health")).toBe("/api/health");
  });
});

describe("the API key", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("is not sent when the build was given none", async () => {
    vi.stubEnv("VITE_API_KEY", "");
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respondWith({})));
    const api = await import("./api");

    await api.fetchJob("job-1");

    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect((init?.headers as Record<string, string>)["X-API-Key"]).toBeUndefined();
  });

  it("is sent on every request when the build has one", async () => {
    vi.stubEnv("VITE_API_KEY", "letmein");
    vi.resetModules();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respondWith({})));
    const api = await import("./api");

    await api.fetchJob("job-1");

    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect((init?.headers as Record<string, string>)["X-API-Key"]).toBe("letmein");
  });

  it("is sent on a download too, which is a separate call", async () => {
    // Exports go through fetch rather than a plain link, so they carry the
    // header. A link could not, and would 401 on a guarded server.
    vi.stubEnv("VITE_API_KEY", "letmein");
    vi.resetModules();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("id,area\n", { status: 200 })),
    );
    vi.stubGlobal("URL", { ...URL, createObjectURL: () => "blob:x", revokeObjectURL: () => {} });
    const api = await import("./api");

    await api.downloadExport("job-1", "csv").catch(() => {
      /* the DOM plumbing of a download is not what is under test */
    });

    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect((init?.headers as Record<string, string>)["X-API-Key"]).toBe("letmein");
  });
});
