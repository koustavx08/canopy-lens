/**
 * Thin API client.
 *
 * Errors from the backend arrive as {error: {code, message, detail}}; we keep
 * that structure intact so the UI can show the server's own explanation rather
 * than a generic "something went wrong".
 */

import type { ApiErrorBody, BoundaryPreview, Capabilities } from "../types";

const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

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
    response = await fetch(apiUrl(path), init);
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
