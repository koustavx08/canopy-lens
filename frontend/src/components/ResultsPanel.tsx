import { useState } from "react";
import { ApiError, downloadExport } from "../lib/api";
import {
  formatArea,
  formatCount,
  formatOptionalArea,
  formatPercent,
  formatSeconds,
} from "../lib/format";
import type { AnalysisResult, BoundaryPreview, Capabilities, ConfidenceBin } from "../types";
import { ConfidenceChart } from "./ConfidenceChart";
import { CrownInspector } from "./CrownInspector";
import { Limitations } from "./Limitations";
import { UncertaintyIndicator } from "./UncertaintyIndicator";

interface Props {
  capabilities: Capabilities | null;
  apiError: string | null;
  boundary: BoundaryPreview | null;
  result: AnalysisResult | null;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

/**
 * Results surface.
 *
 * Every figure here is read from the analysis response. Until one exists the
 * panel shows an empty state rather than sample numbers, because a metric on
 * screen implies a measurement was taken.
 *
 * Three of these are areas and they mean different things, so each is labelled
 * with what it measures rather than left to be inferred:
 *
 * - Analyzed area: the ground examined, and the denominator of coverage.
 * - Total canopy area: the ground under crowns inside it.
 * - Boundary area: what the uploaded file encloses — reported in its own
 *   section, because it comes from the file rather than from a detection.
 */
export function ResultsPanel({
  capabilities,
  apiError,
  boundary,
  result,
  selectedId,
  onSelect,
}: Props) {
  const summary = result?.summary ?? null;

  return (
    <aside
      id="results"
      tabIndex={-1}
      aria-label="Analysis results"
      className="flex w-full shrink-0 flex-col overflow-y-auto border-line bg-surface-1 lg:w-[340px] lg:border-l">
      <section className="border-b border-line p-4 lg:p-5">
        <h2 className="label">Analysis results</h2>

        <dl className="mt-4 flex flex-col">
          <Metric label="Trees detected" value={summary ? formatCount(summary.tree_count) : null} />
          <Metric
            label="Total canopy area"
            note="Ground covered by detected crowns"
            value={summary ? formatOptionalArea(summary.total_canopy_area_m2) : null}
          />
          <Metric
            label="Analyzed area"
            note="Ground examined, nodata excluded"
            value={summary ? formatOptionalArea(summary.analyzed_area_m2) : null}
          />
          <Metric
            label="Canopy coverage"
            note={summary ? coverageNote(summary.coverage_basis) : "Crown area ÷ analyzed area"}
            value={summary ? formatPercent(summary.canopy_coverage_pct) : null}
          />
          <Metric
            label="Average crown area"
            value={summary ? formatOptionalArea(summary.mean_crown_area_m2) : null}
          />
          <Metric
            label="Median crown area"
            value={summary ? formatOptionalArea(summary.median_crown_area_m2) : null}
          />
        </dl>

        {!result && (
          <p className="mt-4 text-[12px] leading-relaxed text-ink-faint">
            {apiError ??
              "No analysis yet. Upload imagery and run a detection to populate these figures — every number here is computed from your file."}
          </p>
        )}

        {result && summary && (
          <>
            {summary.analyzed_area_m2 === null && (
              <p className="mt-4 border-l-2 border-warn pl-3 text-[12px] leading-relaxed text-warn">
                This imagery carries no coordinate system, so nothing can be measured in square
                metres. The figures above are reported in pixels where a pixel figure exists, and
                left blank where it does not.
              </p>
            )}

            <p className="tabular mt-4 font-mono text-[11px] leading-relaxed text-ink-faint">
              {summary.engine} · threshold {summary.confidence_threshold.toFixed(2)} ·{" "}
              {formatCount(summary.raw_detections)} raw ·{" "}
              {formatCount(summary.duplicates_removed)} duplicates removed
              {summary.boundary_applied
                ? ` · ${formatCount(summary.excluded_by_boundary)} outside the boundary`
                : ""}{" "}
              · {formatSeconds(result.processing_time.seconds)}
            </p>

            {result.warnings.length > 0 && (
              <ul className="mt-4 flex flex-col gap-2">
                {result.warnings.map((warning) => (
                  <li
                    key={warning}
                    className="border-l-2 border-warn pl-3 text-[12px] leading-relaxed text-warn"
                  >
                    {warning}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}

        <Downloads result={result} />
      </section>

      {result && (
        <ConfidenceSection
          bins={result.summary.confidence_distribution}
          kind={result.summary.confidence_kind}
          mean={result.summary.mean_confidence}
          median={result.summary.median_confidence}
        />
      )}

      {result && result.quality_diagnostics && (
        <section className="border-b border-line p-4 lg:p-5">
          <UncertaintyIndicator
            diagnostics={result.quality_diagnostics}
            confidenceKind={result.summary.confidence_kind}
          />
        </section>
      )}

      {result && (
        <CrownInspector
          crowns={result.detections}
          selectedId={selectedId}
          onSelect={onSelect}
        />
      )}

      {(result?.boundary_metadata || boundary) && (
        <BoundarySection result={result} preview={boundary} />
      )}

      <Limitations items={result?.limitations.items} notice={result?.limitations.notice} />

      <Method />

      {capabilities && <ServerCapabilities capabilities={capabilities} />}
    </aside>
  );
}

function Metric({
  label,
  value,
  note,
}: {
  label: string;
  value: string | null;
  note?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line/60 py-2 last:border-b-0">
      <dt className="text-[13px] text-ink-muted">
        {label}
        {note && <span className="block text-[11px] text-ink-faint">{note}</span>}
      </dt>
      <dd
        className={`tabular shrink-0 font-mono text-[13px] ${value ? "text-ink" : "text-ink-faint"}`}
      >
        {value ?? "—"}
      </dd>
    </div>
  );
}

function coverageNote(basis: string): string {
  if (basis === "area") return "Crown area ÷ analyzed area";
  if (basis === "pixel") return "Crown pixels ÷ analyzed pixels — not a ground measurement";
  return "Not available for this imagery";
}

/**
 * Both exports, fetched from the backend rather than rebuilt in the browser.
 *
 * A CSV assembled here would be a second implementation of the same numbers,
 * and two implementations of one number eventually disagree.
 */
function Downloads({ result }: { result: AnalysisResult | null }) {
  const [busy, setBusy] = useState<"csv" | "geojson" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const save = async (kind: "csv" | "geojson") => {
    if (!result) return;
    setBusy(kind);
    setError(null);
    try {
      await downloadExport(result.job_id, kind);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "The download could not be produced.",
      );
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div className="mt-4 grid grid-cols-2 gap-2">
        <button
          type="button"
          className="btn-ghost"
          disabled={!result || busy !== null}
          onClick={() => save("csv")}
        >
          {busy === "csv" ? "Preparing…" : "Download CSV"}
        </button>
        <button
          type="button"
          className="btn-ghost"
          disabled={!result || busy !== null}
          onClick={() => save("geojson")}
        >
          {busy === "geojson" ? "Preparing…" : "Download GeoJSON"}
        </button>
      </div>
      {error && <p className="mt-2 text-[12px] leading-relaxed text-danger">{error}</p>}
    </>
  );
}

function ConfidenceSection({
  bins,
  kind,
  mean,
  median,
}: {
  bins: ConfidenceBin[];
  kind: string;
  mean: number | null;
  median: number | null;
}) {
  const heuristic = kind !== "model";
  return (
    <section className="border-b border-line p-4 lg:p-5">
      <h2 className="label">{heuristic ? "Score distribution" : "Confidence distribution"}</h2>
      <ConfidenceChart bins={bins} />
      <p className="tabular mt-3 font-mono text-[11px] text-ink-faint">
        mean {mean === null ? "—" : mean.toFixed(3)} · median{" "}
        {median === null ? "—" : median.toFixed(3)}
      </p>
      <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
        {heuristic
          ? "These are heuristic scores: candidates ranked by shape and colour. They are not probabilities, and a high score is not evidence that a crown is real."
          : "The detector's own scores for the crowns it kept. A score is how sure the model is, not how often it is right."}
      </p>
    </section>
  );
}

/**
 * The boundary as the analysis applied it.
 *
 * The area the file encloses and the part of it the imagery covers are shown
 * as two figures, because when they differ the second is what the coverage
 * percentage was computed against.
 */
function BoundarySection({
  result,
  preview,
}: {
  result: AnalysisResult | null;
  preview: BoundaryPreview | null;
}) {
  const applied = result?.boundary_metadata ?? null;
  const areaM2 = applied?.area_m2 ?? preview?.area_m2 ?? null;
  const parts = applied?.part_count ?? preview?.part_count ?? null;

  return (
    <section className="border-b border-line p-4 lg:p-5">
      <h2 className="label">Boundary</h2>
      <dl className="mt-4 flex flex-col">
        <Metric
          label="Boundary area"
          note="Ground the file encloses"
          value={areaM2 === null ? null : formatArea(areaM2)}
        />
        {applied?.area_within_imagery_m2 !== null && applied?.area_within_imagery_m2 !== undefined && (
          <Metric
            label="Covered by imagery"
            note="The part of it that could be analysed"
            value={`${formatArea(applied.area_within_imagery_m2)}${
              applied.imagery_covers_pct === null
                ? ""
                : ` · ${formatPercent(applied.imagery_covers_pct, 1)}`
            }`}
          />
        )}
        <Metric label="Separate areas" value={parts === null ? null : String(parts)} />
        {applied && (
          <Metric
            label="Detections excluded"
            note="Crowns found outside the boundary"
            value={formatCount(applied.detections_excluded)}
          />
        )}
      </dl>
      {preview && (
        <p className="mt-2 text-[12px] leading-relaxed text-ink-faint">
          Read from {preview.source_format} in {preview.source_crs} and measured in{" "}
          {preview.measurement_crs}. This is what the boundary encloses, not what the imagery
          covers — the analyzed area is smaller wherever the image stops short of it.
        </p>
      )}
    </section>
  );
}

/** How the numbers above were produced, for anyone who has to defend them. */
function Method() {
  return (
    <section className="border-b border-line p-4 lg:p-5">
      <details className="group">
        <summary className="label cursor-pointer list-none select-none hover:text-ink-muted">
          Method
        </summary>
        <ol className="mt-3 flex list-decimal flex-col gap-2 pl-4 text-[12px] leading-relaxed text-ink-faint marker:text-line-strong">
          <li>
            The imagery is read server-side and, if it is large, cut into overlapping tiles so
            nothing has to fit in memory at once.
          </li>
          <li>
            Each tile is run through the detection engine. Crowns found twice in the overlap are
            merged, so a tile seam does not become two trees.
          </li>
          <li>
            Every crown is reprojected into a metric coordinate system before its area is
            computed. Areas are never calculated from degrees of latitude and longitude, which do
            not measure the same distance in both directions.
          </li>
          <li>
            If a boundary was supplied, crowns are clipped to it and the analysed area becomes the
            part of it the imagery actually covers.
          </li>
          <li>
            Coverage is total crown area divided by that analysed area — never by the boundary
            area, which would understate coverage wherever the imagery falls short.
          </li>
        </ol>
      </details>
    </section>
  );
}

function ServerCapabilities({ capabilities }: { capabilities: Capabilities }) {
  return (
    <section className="border-t border-line p-4 lg:p-5">
      <details className="group">
        <summary className="label cursor-pointer list-none select-none hover:text-ink-muted">
          Server capabilities
        </summary>
        <ul className="mt-3 flex flex-col gap-1.5">
          {capabilities.dependencies.map((dependency) => (
            <li
              key={dependency.module}
              className="flex items-baseline justify-between font-mono text-[11px]"
            >
              <span className="text-ink-muted">{dependency.module}</span>
              <span className={dependency.installed ? "text-ink-faint" : "text-warn"}>
                {dependency.installed ? (dependency.version ?? "installed") : "not installed"}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-[11px] leading-relaxed text-ink-faint">
          Detection engines available: {capabilities.engines.filter((e) => e.available).length} of{" "}
          {capabilities.engines.length}.
        </p>
      </details>
    </section>
  );
}
