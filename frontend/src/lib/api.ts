/**
 * Thin API client.
 *
 * Errors from the backend arrive as {error: {code, message, detail}}; we keep
 * that structure intact so the UI can show the server's own explanation rather
 * than a generic "something went wrong".
 */

import type { ApiErrorBody, BoundaryPreview, Capabilities } from "../types";

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

/**
 * The key this build sends, when the backend it talks to asks for one.
 *
 * Compiled into the bundle, so it is readable by anyone who opens the page.
 * That is not a flaw in how it is stored here - a browser has nowhere to keep
 * a secret from its own user - it is what a key of this kind is for: it keeps
 * a public address from being usable by anything that has not been handed the
 * build, which stops drive-by traffic and costs an attacker one view-source.
 * An instance holding data worth protecting wants a real session, not this.
 */
const API_KEY = (import.meta.env.VITE_API_KEY ?? "").trim();

function authHeaders(): Record<string, string> {
  return API_KEY ? { "X-API-Key": API_KEY } : {};
}

export class ApiError extends Error {
  readonly code: string;
  readonly detail: Record<string, unknown>;
  readonly status: number;

  constructor(body: ApiErrorBody, status: number) {
    super(body.message);
    this.name = "ApiError";
    this.code = body.code;
    this.detail = body.detail ?? {};
    this.status = status;
  }
}

export function apiUrl(path: string): string {
  return `${BASE_URL}${path}`;
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      ...init,
      headers: { ...authHeaders(), ...((init?.headers as Record<string, string>) ?? {}) },
    });
  } catch (cause) {
    throw new ApiError(
      {
        code: "network_unreachable",
        message: "Could not reach the CanopyLens backend.",
        detail: { cause: String(cause) },
      },
      0,
    );
  }

  if (!response.ok) {
    let body: ApiErrorBody = {
      code: `http_${response.status}`,
      message: response.statusText || "Request failed.",
      detail: {},
    };
    try {
      const parsed = await response.json();
      if (parsed?.error?.message) body = parsed.error as ApiErrorBody;
    } catch {
      /* response had no JSON body; keep the status-derived message */
    }
    throw new ApiError(body, response.status);
  }

  return (await response.json()) as T;
}

export function fetchCapabilities(signal?: AbortSignal): Promise<Capabilities> {
  return request<Capabilities>("/api/capabilities", { signal });
}

/**
 * Parse and measure a boundary file server-side.
 *
 * The KML is not parsed in the browser: the area has to be computed in a
 * projected coordinate system, and the one place that reprojection is done
 * correctly is the backend. A number produced twice by two code paths is a
 * number that will eventually disagree with itself.
 */
export function previewBoundary(file: File, signal?: AbortSignal): Promise<BoundaryPreview> {
  const body = new FormData();
  body.append("file", file);
  return request<BoundaryPreview>("/api/boundary/preview", { method: "POST", body, signal });
}

// ---------------------------------------------------------------------------
// Analysis
// ---------------------------------------------------------------------------

import type {
  AnalysisResult,
  DetectionSettings,
  JobAccepted,
  JobStatus,
} from "../types";

/**
 * Submit imagery for analysis.
 *
 * Uses XMLHttpRequest rather than fetch for one reason: it is the only way to
 * observe upload progress. A GeoTIFF can be hundreds of megabytes, and an
 * indeterminate spinner over a five-minute upload tells the user nothing about
 * whether anything is happening.
 */
export function startAnalysis(
  image: File,
  boundary: File | null,
  settings: DetectionSettings,
  options: { onUploadProgress?: (fraction: number) => void; signal?: AbortSignal } = {},
): Promise<JobAccepted> {
  const body = new FormData();
  body.append("image", image);
  if (boundary) body.append("boundary", boundary);
  body.append("confidence_threshold", String(settings.confidenceThreshold));
  body.append("min_crown_area_m2", String(settings.minCrownAreaM2));
  if (settings.engine) body.append("engine", settings.engine);

  return new Promise<JobAccepted>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", apiUrl("/api/analyze"));
    xhr.responseType = "text";
    for (const [name, value] of Object.entries(authHeaders())) xhr.setRequestHeader(name, value);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) options.onUploadProgress?.(event.loaded / event.total);
    };

    xhr.onerror = () =>
      reject(
        new ApiError(
          {
            code: "network_unreachable",
            message: "Could not reach the CanopyLens backend.",
            detail: {},
          },
          0,
        ),
      );

    xhr.onabort = () => reject(new DOMException("Aborted", "AbortError"));

    xhr.onload = () => {
      let parsed: unknown = null;
      try {
        parsed = JSON.parse(xhr.responseText);
      } catch {
        /* fall through to the status-derived message below */
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(parsed as JobAccepted);
        return;
      }
      const envelope = (parsed as { error?: ApiErrorBody } | null)?.error;
      reject(
        new ApiError(
          envelope?.message
            ? envelope
            : {
                code: `http_${xhr.status}`,
                message: xhr.statusText || "The analysis could not be started.",
                detail: {},
              },
          xhr.status,
        ),
      );
    };

    options.signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(body);
  });
}

export function fetchJob(jobId: string, signal?: AbortSignal): Promise<JobStatus> {
  return request<JobStatus>(`/api/jobs/${encodeURIComponent(jobId)}`, { signal });
}

export function fetchResult(jobId: string, signal?: AbortSignal): Promise<AnalysisResult> {
  return request<AnalysisResult>(`/api/results/${encodeURIComponent(jobId)}`, { signal });
}

/**
 * Fetch an export and hand it to the browser as a download.
 *
 * Deliberately not a plain link: a link to a failed job downloads the JSON
 * error body as a file called `crowns.csv`, which is a broken file the user has
 * to open to understand. Fetching first means a failure is reported as one.
 */
export async function downloadExport(jobId: string, kind: "csv" | "geojson"): Promise<void> {
  const path = `/api/results/${encodeURIComponent(jobId)}/${kind}`;
  let response: Response;
  try {
    response = await fetch(apiUrl(path), { headers: authHeaders() });
  } catch (cause) {
    throw new ApiError(
      { code: "network_unreachable", message: "Could not reach the backend.", detail: { cause: String(cause) } },
      0,
    );
  }
  if (!response.ok) {
    let body: ApiErrorBody = {
      code: `http_${response.status}`,
      message: "The download could not be produced.",
      detail: {},
    };
    try {
      const parsed = await response.json();
      if (parsed?.error?.message) body = parsed.error as ApiErrorBody;
    } catch {
      /* keep the status-derived message */
    }
    throw new ApiError(body, response.status);
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filenameFrom(response) ?? `crowns-${jobId}.${kind}`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** The server names the file; we only fall back if it did not. */
function filenameFrom(response: Response): string | null {
  const header = response.headers.get("Content-Disposition");
  const match = header?.match(/filename="?([^"\r\n;]+)"?/i);
  return match ? match[1] : null;
}
