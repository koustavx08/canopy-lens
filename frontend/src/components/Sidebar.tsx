import { Dropzone } from "./Dropzone";
import { formatArea } from "../lib/format";
import type { BoundaryPreview, Capabilities, DetectionSettings } from "../types";

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
  runBlockedReason: string | null;
  running: boolean;
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
  runBlockedReason,
  running,
}: Props) {
  const maxBytes = (capabilities?.limits.max_upload_mb ?? 512) * 1024 * 1024;
  const maxBoundaryBytes = (capabilities?.limits.max_boundary_upload_mb ?? 32) * 1024 * 1024;

  return (
    <aside className="flex w-full shrink-0 flex-col gap-6 overflow-y-auto border-line bg-surface-1 p-4 lg:w-[340px] lg:border-r lg:p-5">
      <Dropzone
        label="Upload imagery"
        hint="Drop a GeoTIFF, PNG or JPEG"
        accept={capabilities?.supported_raster_formats ?? FALLBACK_RASTER}
        file={imageFile}
        onSelect={onImageSelect}
        maxBytes={maxBytes}
      />

      <div>
        <Dropzone
          label="Forest boundary"
          hint="Drop a KML, KMZ or GeoJSON"
          accept={capabilities?.supported_boundary_formats ?? FALLBACK_BOUNDARY}
          file={boundaryFile}
          onSelect={onBoundarySelect}
          maxBytes={maxBoundaryBytes}
          optional
        />
        {boundaryFile && <BoundaryStatus
          preview={boundaryPreview}
          error={boundaryError}
          loading={boundaryLoading}
        />}
      </div>

      <section className="flex flex-col gap-4 border-t border-line pt-5">
        <span className="label">Detection settings</span>

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
            onChange={(event) =>
              onSettingsChange({
                ...settings,
                confidenceThreshold: Number(event.target.value),
              })
            }
            className="w-full accent-accent"
          />
          <p className="mt-1.5 text-[11px] leading-relaxed text-ink-faint">
            Higher values keep only the crowns the model is most sure about, and will miss real
            trees. Lower values find more trees and more false positives.
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
            step={0.5}
            value={settings.minCrownAreaM2}
            onChange={(event) =>
              onSettingsChange({
                ...settings,
                minCrownAreaM2: Math.max(0, Number(event.target.value)),
              })
            }
            className="field tabular"
          />
          <p className="mt-1.5 text-[11px] leading-relaxed text-ink-faint">
            Detections smaller than this are discarded. Requires georeferenced imagery; without a
            known ground sample distance the filter cannot be applied in metres.
          </p>
        </div>
      </section>

      <div className="mt-auto border-t border-line pt-5">
        <button
          type="button"
          className="btn-primary w-full"
          disabled={Boolean(runBlockedReason) || running}
          onClick={onRun}
        >
          {running ? "Analysing…" : "Run analysis"}
        </button>
        {runBlockedReason && (
          <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">{runBlockedReason}</p>
        )}
      </div>
    </aside>
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
