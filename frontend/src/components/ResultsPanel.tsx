import { formatArea } from "../lib/format";
import type { BoundaryPreview, Capabilities } from "../types";
import { Limitations } from "./Limitations";

interface Props {
  capabilities: Capabilities | null;
  apiError: string | null;
  boundary: BoundaryPreview | null;
}

/**
 * Three of these are areas and they mean different things, so each is labelled
 * with what it measures rather than left to be inferred:
 *
 * - Analyzed area: the ground examined, and the denominator of coverage.
 * - Detected crown area: the ground under crowns inside it.
 * - Boundary area: what the uploaded KML encloses — reported in its own
 *   section, because it comes from the file rather than from a detection.
 */
const METRICS: { label: string; note?: string }[] = [
  { label: "Trees detected" },
  { label: "Detected crown area", note: "Ground covered by crowns" },
  { label: "Analyzed area", note: "Ground examined, nodata excluded" },
  { label: "Canopy coverage", note: "Crown area ÷ analyzed area" },
  { label: "Average crown area" },
  { label: "Median crown area" },
];

/**
 * Results surface. Until a real analysis has run it shows an empty state —
 * never sample numbers, because a metric on screen implies a measurement.
 */
export function ResultsPanel({ capabilities, apiError, boundary }: Props) {
  return (
    <aside className="flex w-full shrink-0 flex-col overflow-y-auto border-line bg-surface-1 lg:w-[320px] lg:border-l">
      <section className="border-b border-line p-4 lg:p-5">
        <span className="label">Analysis results</span>

        <dl className="mt-4 flex flex-col">
          {METRICS.map((metric) => (
            <div
              key={metric.label}
              className="flex items-baseline justify-between gap-3 border-b border-line/60 py-2 last:border-b-0"
            >
              <dt className="text-[13px] text-ink-muted">
                {metric.label}
                {metric.note && (
                  <span className="block text-[11px] text-ink-faint">{metric.note}</span>
                )}
              </dt>
              <dd className="tabular font-mono text-[13px] text-ink-faint">—</dd>
            </div>
          ))}
        </dl>

        <p className="mt-4 text-[12px] leading-relaxed text-ink-faint">
          {apiError
            ? apiError
            : "No analysis yet. Upload imagery and run a detection to populate these figures — every number here is computed from your file."}
        </p>

        <div className="mt-4 grid grid-cols-2 gap-2">
          <button type="button" className="btn-ghost" disabled>
            Download CSV
          </button>
          <button type="button" className="btn-ghost" disabled>
            Download GeoJSON
          </button>
        </div>
      </section>

      {boundary && (
        <section className="border-b border-line p-4 lg:p-5">
          <span className="label">Boundary</span>
          <dl className="mt-4 flex flex-col">
            <div className="flex items-baseline justify-between gap-3 border-b border-line/60 py-2">
              <dt className="text-[13px] text-ink-muted">
                Boundary area
                <span className="block text-[11px] text-ink-faint">Ground the file encloses</span>
              </dt>
              <dd className="tabular font-mono text-[13px] text-ink">
                {formatArea(boundary.area_m2)}
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-3 py-2">
              <dt className="text-[13px] text-ink-muted">Separate areas</dt>
              <dd className="tabular font-mono text-[13px] text-ink">{boundary.part_count}</dd>
            </div>
          </dl>
          <p className="mt-2 text-[12px] leading-relaxed text-ink-faint">
            Read from {boundary.source_format} in {boundary.source_crs} and measured in{" "}
            {boundary.measurement_crs}. This is what the boundary encloses, not what the imagery
            covers — the analyzed area will be smaller wherever the image stops short of it.
          </p>
        </section>
      )}

      <Limitations />

      {capabilities && (
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
              Detection engines available: {capabilities.engines.filter((e) => e.available).length}{" "}
              of {capabilities.engines.length}.
            </p>
          </details>
        </section>
      )}
    </aside>
  );
}
