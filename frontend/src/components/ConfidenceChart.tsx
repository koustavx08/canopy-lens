import type { ConfidenceBin } from "../types";

/**
 * The distribution of detection scores, as plain bars.
 *
 * Horizontal rather than vertical because the panel is narrow and each bin
 * needs its range spelled out beside it; and a list rather than a canvas
 * because the numbers should be readable by a screen reader, not just visible.
 * Bars are scaled against the largest bin, and the scale is stated so a long
 * bar is never mistaken for a large absolute count.
 */
export function ConfidenceChart({ bins }: { bins: ConfidenceBin[] }) {
  const total = bins.reduce((sum, bin) => sum + bin.count, 0);
  if (total === 0) {
    return <p className="mt-3 text-[12px] text-ink-faint">No detections to summarise.</p>;
  }
  const peak = Math.max(...bins.map((bin) => bin.count));

  return (
    <ul className="mt-3 flex flex-col gap-1">
      {bins.map((bin) => (
        <li key={`${bin.lower}-${bin.upper}`} className="flex items-center gap-2">
          <span className="tabular w-[62px] shrink-0 font-mono text-[11px] text-ink-faint">
            {bin.lower.toFixed(2)}–{bin.upper.toFixed(2)}
          </span>
          <span
            className="h-2.5 min-w-0 flex-1 bg-surface-2"
            role="img"
            aria-label={`${bin.count} crowns scored between ${bin.lower.toFixed(2)} and ${bin.upper.toFixed(2)}`}
          >
            <span
              className="block h-full bg-accent"
              style={{ width: `${peak === 0 ? 0 : (bin.count / peak) * 100}%` }}
            />
          </span>
          <span className="tabular w-9 shrink-0 text-right font-mono text-[11px] text-ink">
            {bin.count}
          </span>
        </li>
      ))}
    </ul>
  );
}
