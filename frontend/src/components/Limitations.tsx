/**
 * The standing limits of the method.
 *
 * Every analysis response carries its own copy of this list, and that copy is
 * authoritative — it is the one that travels with the numbers into a CSV or a
 * report. The list below is what is shown before any analysis has run, when
 * there is no response to take it from, and mirrors `backend/app/limitations.py`.
 */
const FALLBACK_NOTICE =
  "Results are model estimates, not measurements. Treat crown counts and areas as approximate, and verify before using them for any decision that matters.";

const FALLBACK_ITEMS = [
  "Overlapping and touching crowns in closed canopy are frequently merged or split incorrectly.",
  "Dense forest, deep shadow and steep terrain reduce accuracy substantially.",
  "Imagery coarser than roughly 0.5 m per pixel cannot resolve individual small crowns.",
  "Crowns clipped by the image edge or the boundary are measured only where visible.",
  "Shrubs, hedges, crops and grass can be detected as trees; the model sees vegetation, not species.",
  "Models are trained on specific regions and sensors; results shift on unfamiliar landscapes (domain shift).",
  "A boundary is taken as drawn: the polygon is measured and clipped to, not checked against where the forest actually is.",
  "Coverage is crown area divided by the analysed area — the ground the imagery actually covers, which may be smaller than the boundary.",
  "No ground-truth validation is performed. Nothing here is a field-verified tree inventory.",
];

interface Props {
  /** From the analysis response, when one exists. */
  items?: string[];
  notice?: string;
}

export function Limitations({ items, notice }: Props) {
  const list = items?.length ? items : FALLBACK_ITEMS;

  return (
    <section className="border-b border-line p-4 lg:p-5">
      <h2 className="label">Limitations</h2>
      <p className="mt-3 text-[12px] leading-relaxed text-ink-muted">
        {notice ?? FALLBACK_NOTICE}
      </p>
      <ul className="mt-3 flex flex-col gap-2">
        {list.map((item) => (
          <li key={item} className="flex gap-2 text-[12px] leading-relaxed text-ink-faint">
            <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-line-strong" aria-hidden />
            {item}
          </li>
        ))}
      </ul>
    </section>
  );
}
