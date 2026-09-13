import { formatArea } from "../lib/format";
import type { Detection } from "../types";

interface Props {
  crowns: Detection[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

/**
 * One crown, in detail.
 *
 * The previous/next controls exist for more than convenience: clicking a
 * polygon on a map is a mouse-only gesture, and stepping through the crowns
 * from the keyboard is the way this is usable without one.
 */
export function CrownInspector({ crowns, selectedId, onSelect }: Props) {
  const index = crowns.findIndex((crown) => crown.id === selectedId);
  const crown = index === -1 ? null : crowns[index];

  if (!crown) {
    return (
      <section className="border-b border-line p-4 lg:p-5">
        <h2 className="label">Crown inspector</h2>
        <p className="mt-3 text-[12px] leading-relaxed text-ink-faint">
          {crowns.length === 0
            ? "No crowns to inspect yet."
            : "Select a crown on the map, or step through them below, to see its measurements."}
        </p>
        {crowns.length > 0 && (
          <button
            type="button"
            className="btn-ghost mt-3 w-full"
            onClick={() => onSelect(crowns[0].id)}
          >
            Inspect first crown
          </button>
        )}
      </section>
    );
  }

  // A heuristic score ranks candidates by shape and colour. It is not a
  // probability, and labelling it "confidence" without saying so would be a
  // claim the number cannot support.
  const heuristic = crown.confidence_kind !== "model";

  return (
    <section className="border-b border-line p-4 lg:p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="label">Crown inspector</h2>
        <span className="tabular font-mono text-[11px] text-ink-faint">
          {index + 1} of {crowns.length}
        </span>
      </div>

      <p className="tabular mt-3 font-mono text-[15px] text-ink">{crown.id}</p>

      <dl className="mt-3 flex flex-col">
        <Row
          term={heuristic ? "Heuristic score" : "Model confidence"}
          note={heuristic ? "A shape-and-colour ranking, not a probability" : undefined}
          value={crown.confidence.toFixed(3)}
        />
        <Row
          term="Crown area"
          note={crown.area_m2 === null ? "No ground scale in this imagery" : "Measured on the ground"}
          value={
            crown.area_m2 === null
              ? `${crown.pixel_area.toLocaleString()} px²`
              : formatArea(crown.area_m2)
          }
        />
        <Row term="Pixel area" value={`${crown.pixel_area.toLocaleString()} px²`} />
        <Row
          term="Centroid"
          note={crown.centroid_lat === null ? "Image pixel coordinates" : "WGS84 (EPSG:4326)"}
          value={
            crown.centroid_lat === null || crown.centroid_lon === null
              ? `${crown.centroid_x_px}, ${crown.centroid_y_px} px`
              : `${crown.centroid_lat.toFixed(6)}, ${crown.centroid_lon.toFixed(6)}`
          }
        />
        <Row term="Engine" value={crown.engine} />
      </dl>

      <div className="mt-4 flex gap-2">
        <button
          type="button"
          className="btn-ghost flex-1"
          onClick={() => onSelect(crowns[(index - 1 + crowns.length) % crowns.length].id)}
          disabled={crowns.length < 2}
        >
          ← Previous
        </button>
        <button
          type="button"
          className="btn-ghost flex-1"
          onClick={() => onSelect(crowns[(index + 1) % crowns.length].id)}
          disabled={crowns.length < 2}
        >
          Next →
        </button>
      </div>
      <button
        type="button"
        className="mt-2 w-full rounded-md px-3 py-1.5 text-[12px] text-ink-muted hover:bg-surface-2 hover:text-ink"
        onClick={() => onSelect(null)}
      >
        Clear selection
      </button>
    </section>
  );
}

function Row({ term, value, note }: { term: string; value: string; note?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-line/60 py-2 last:border-b-0">
      <dt className="text-[13px] text-ink-muted">
        {term}
        {note && <span className="block text-[11px] text-ink-faint">{note}</span>}
      </dt>
      <dd className="tabular shrink-0 text-right font-mono text-[13px] text-ink">{value}</dd>
    </div>
  );
}
