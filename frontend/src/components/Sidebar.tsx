import { Dropzone } from "./Dropzone";
import { formatArea } from "../lib/format";
import type { ApiError } from "../lib/api";
import type {
  AnalysisPhase,
  BoundaryPreview,
  Capabilities,
  DetectionSettings,
  JobStatus,
} from "../types";

interface Props {
  capabilities: Capabilities | null;
  imageFile: File | null;
  boundaryFile: File | null;
  boundaryPreview: BoundaryPreview | null;
  boundaryError: string | null;
  boundaryLoading: boolean;
  settings: DetectionSettings;
  onImageSelect: (file: File | null) => void;
  onBoundarySelect: (file: File | null) => void;
  onSettingsChange: (settings: DetectionSettings) => void;
  onRun: () => void;
  onCancel: () => void;
  runBlockedReason: string | null;
  phase: AnalysisPhase;
  job: JobStatus | null;
  uploadFraction: number | null;
  error: ApiError | null;
}

const FALLBACK_RASTER = [".tif", ".tiff", ".png", ".jpg", ".jpeg"];
const FALLBACK_BOUNDARY = [".kml", ".kmz", ".geojson"];

export function Sidebar({
  capabilities,
  imageFile,
  boundaryFile,
  boundaryPreview,
  boundaryError,
  boundaryLoading,
  settings,
  onImageSelect,
  onBoundarySelect,
  onSettingsChange,
  onRun,
  onCancel,
  runBlockedReason,
  phase,
  job,
  uploadFraction,
  error,
}: Props) {
  const maxBytes = (capabilities?.limits.max_upload_mb ?? 512) * 1024 * 1024;
  const maxBoundaryBytes = (capabilities?.limits.max_boundary_upload_mb ?? 32) * 1024 * 1024;
  const busy = phase === "uploading" || phase === "processing" || phase === "post-processing";
  const engines = capabilities?.engines ?? [];

  return (
    <aside className="flex w-full shrink-0 flex-col gap-6 overflow-y-auto border-line bg-surface-1 p-4 lg:w-[340px] lg:border-r lg:p-5">
      <Dropzone
        label="Upload imagery"
        hint="Drop a GeoTIFF, PNG or JPEG"
        accept={capabilities?.supported_raster_formats ?? FALLBACK_RASTER}
        file={imageFile}
        onSelect={onImageSelect}
        maxBytes={maxBytes}
        disabled={busy}
      />

      <div>
        <Dropzone
          label="Forest boundary"
          hint="Drop a KML, KMZ or GeoJSON"
          accept={capabilities?.supported_boundary_formats ?? FALLBACK_BOUNDARY}
          file={boundaryFile}
          onSelect={onBoundarySelect}
          maxBytes={maxBoundaryBytes}
          disabled={busy}
          optional
        />
        {boundaryFile && (
          <BoundaryStatus
            preview={boundaryPreview}
            error={boundaryError}
            loading={boundaryLoading}
          />
        )}
      </div>

      <section className="flex flex-col gap-4 border-t border-line pt-5">
        <h2 className="label">Detection settings</h2>

        <div>
          <div className="mb-2 flex items-baseline justify-between">
            <label htmlFor="confidence" className="text-[13px] text-ink-muted">
              Confidence threshold
            </label>
            <span className="tabular font-mono text-[12px] text-ink">
              {settings.confidenceThreshold.toFixed(2)}
            </span>
          </div>
          <input
            id="confidence"
            type="range"
            min={0.05}
            max={0.95}
            step={0.05}
            value={settings.confidenceThreshold}
            disabled={busy}
            aria-describedby="confidence-help"
            onChange={(event) =>
              onSettingsChange({ ...settings, confidenceThreshold: Number(event.target.value) })
            }
            className="w-full accent-accent disabled:opacity-50"
          />
          <p id="confidence-help" className="mt-1.5 text-[11px] leading-relaxed text-ink-faint">
            Higher values keep only the crowns the model is most sure about, and will miss real
            trees. Lower values find more trees and more false positives. There is no setting that
            avoids both.
          </p>
        </div>

        <div>
          <label htmlFor="min-area" className="mb-2 block text-[13px] text-ink-muted">
            Minimum crown area (m²)
          </label>
          <input
            id="min-area"
            type="number"
            min={0}
            max={100000}
            step={0.5}
            value={settings.minCrownAreaM2}
            disabled={busy}
            aria-describedby="min-area-help"
            onChange={(event) =>
              onSettingsChange({
                ...settings,
                minCrownAreaM2: clamp(Number(event.target.value), 0, 100000),
              })
            }
            className="field tabular disabled:opacity-50"
          />
          <p id="min-area-help" className="mt-1.5 text-[11px] leading-relaxed text-ink-faint">
            Detections smaller than this are discarded — useful for dropping shrubs and speckle.
            Needs georeferenced imagery: without a known ground sample distance there are no square
            metres to filter on, and the setting is ignored.
          </p>
        </div>

        {engines.length > 1 && (
          <div>
            <label htmlFor="engine" className="mb-2 block text-[13px] text-ink-muted">
              Detection engine
            </label>
            <select
              id="engine"
              value={settings.engine}
              disabled={busy}
              aria-describedby="engine-help"
              onChange={(event) => onSettingsChange({ ...settings, engine: event.target.value })}
              className="field disabled:opacity-50"
            >
              <option value="auto">Auto — best available</option>
              {engines.map((engine) => (
                <option key={engine.name} value={engine.name} disabled={!engine.available}>
                  {engine.title}
                  {engine.available ? "" : " (unavailable)"}
                </option>
              ))}
            </select>
            <p id="engine-help" className="mt-1.5 text-[11px] leading-relaxed text-ink-faint">
              {engines.find((engine) => engine.name === settings.engine)?.description ??
                "Auto picks the best engine this server can actually run."}
            </p>
          </div>
        )}
      </section>

      <div className="mt-auto border-t border-line pt-5">
        <Progress phase={phase} job={job} uploadFraction={uploadFraction} error={error} />

        <button
          type="button"
          className="btn-primary w-full"
          disabled={Boolean(runBlockedReason) || busy}
          onClick={onRun}
        >
          {busy ? "Analysing…" : phase === "complete" ? "Run again" : "Run analysis"}
        </button>

        {busy && (
          <button
            type="button"
            className="mt-2 w-full rounded-md px-3 py-1.5 text-[12px] text-ink-muted hover:bg-surface-2 hover:text-ink"
            onClick={onCancel}
          >
            Stop watching this analysis
          </button>
        )}

        {runBlockedReason && !busy && (
          <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">{runBlockedReason}</p>
        )}
      </div>
    </aside>
  );
}

const PHASE_LABELS: Record<AnalysisPhase, string> = {
  idle: "",
  uploading: "Uploading",
  processing: "Processing",
  "post-processing": "Post-processing",
  complete: "Complete",
  error: "Error",
};

/**
 * What the analysis is doing, from the job's own report.
 *
 * The bar tracks bytes sent while uploading and tiles finished while detecting.
 * It never advances on a timer: a progress bar driven by elapsed time tells the
 * user how long they have waited, which they already know, and implies a
 * completion estimate the server has not made. So a single-tile image jumps
 * rather than sweeping — that is the real shape of the work.
 */
function Progress({
  phase,
  job,
  uploadFraction,
  error,
}: {
  phase: AnalysisPhase;
  job: JobStatus | null;
  uploadFraction: number | null;
  error: ApiError | null;
}) {
  if (phase === "idle") return null;

  const fraction =
    phase === "uploading" ? (uploadFraction ?? 0) : phase === "complete" ? 1 : (job?.progress ?? 0);
  const determinate = phase !== "error" && (phase !== "uploading" || uploadFraction !== null);

  return (
    <div className="mb-4">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <span
          className={`text-[12px] ${phase === "error" ? "text-danger" : "text-ink"}`}
          role="status"
        >
          {PHASE_LABELS[phase]}
          {job && phase !== "complete" && phase !== "error" ? ` · ${job.stage}` : ""}
        </span>
        {determinate && phase !== "complete" && (
          <span className="tabular font-mono text-[11px] text-ink-faint">
            {Math.round(fraction * 100)}%
          </span>
        )}
      </div>

      {phase !== "error" && (
        <div
          className="h-1 w-full overflow-hidden bg-surface-2"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(fraction * 100)}
          aria-label={`${PHASE_LABELS[phase]} progress`}
        >
          <div
            className="h-full bg-accent transition-[width] duration-300"
            style={{ width: `${Math.round(fraction * 100)}%` }}
          />
        </div>
      )}

      {phase === "processing" && (
        <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
          Counting tiles completed, not seconds elapsed. Large imagery takes minutes; the page stays
          usable while it runs.
        </p>
      )}

      {phase === "error" && error && (
        <p className="mt-1 border-l-2 border-danger pl-3 text-[12px] leading-relaxed text-danger">
          {error.message}
        </p>
      )}
    </div>
  );
}

/**
 * What the server made of the boundary file, shown next to the file itself.
 *
 * The area here is the ground the boundary encloses and is labelled as such.
 * It is not the analysed area and not the canopy area; the results panel
 * reports those separately once an analysis has run.
 */
function BoundaryStatus({
  preview,
  error,
  loading,
}: {
  preview: BoundaryPreview | null;
  error: string | null;
  loading: boolean;
}) {
  if (loading) {
    return <p className="mt-2 text-[11px] text-ink-faint">Reading boundary…</p>;
  }
  if (error) {
    return <p className="mt-2 text-[11px] leading-relaxed text-danger">{error}</p>;
  }
  if (!preview) return null;
  return (
    <dl className="mt-2 flex flex-col gap-1 border-l border-line pl-3 text-[11px]">
      <div className="flex items-baseline justify-between gap-3">
        <dt className="text-ink-faint">Boundary area</dt>
        <dd className="tabular font-mono text-ink">{formatArea(preview.area_m2)}</dd>
      </div>
      <div className="flex items-baseline justify-between gap-3">
        <dt className="text-ink-faint">Parts</dt>
        <dd className="tabular font-mono text-ink">
          {preview.part_count}
          {preview.has_holes ? " (with holes)" : ""}
        </dd>
      </div>
      <div className="flex items-baseline justify-between gap-3">
        <dt className="text-ink-faint">Measured in</dt>
        <dd className="tabular font-mono text-ink">{preview.measurement_crs}</dd>
      </div>
      {preview.warnings.map((warning) => (
        <p key={warning} className="mt-1 leading-relaxed text-warn">
          {warning}
        </p>
      ))}
    </dl>
  );
}

function clamp(value: number, low: number, high: number): number {
  if (Number.isNaN(value)) return low;
  return Math.min(high, Math.max(low, value));
}
