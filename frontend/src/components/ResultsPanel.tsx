import type { Capabilities } from "../types";
import { Limitations } from "./Limitations";

interface Props {
  capabilities: Capabilities | null;
  apiError: string | null;
}

const METRICS = [
  "Trees detected",
  "Total canopy area",
  "Analyzed area",
  "Canopy coverage",
  "Average crown area",
  "Median crown area",
];

/**
 * Results surface. Until a real analysis has run it shows an empty state —
 * never sample numbers, because a metric on screen implies a measurement.
 */
export function ResultsPanel({ capabilities, apiError }: Props) {
  return (
    <aside className="flex w-full shrink-0 flex-col overflow-y-auto border-line bg-surface-1 lg:w-[320px] lg:border-l">
      <section className="border-b border-line p-4 lg:p-5">
        <span className="label">Analysis results</span>

        <dl className="mt-4 flex flex-col">
          {METRICS.map((metric) => (
            <div
              key={metric}
              className="flex items-baseline justify-between border-b border-line/60 py-2 last:border-b-0"
            >
              <dt className="text-[13px] text-ink-muted">{metric}</dt>
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
