import type { Detection, QualityDiagnostics } from "../types";

interface Props {
  diagnostics?: QualityDiagnostics | null;
  selectedCrown?: Detection | null;
  confidenceKind?: string;
}

export function UncertaintyIndicator({
  diagnostics,
  selectedCrown,
  confidenceKind = "model",
}: Props) {
  const flags = selectedCrown?.uncertainty_flags ?? [];
  const needsReview = flags.includes("human_review_recommended");
  const calibrated = selectedCrown?.calibrated_confidence;
  const raw = selectedCrown?.confidence;

  return (
    <div className="flex flex-col gap-3 rounded-md border border-line bg-surface-2 p-3 text-[12px]">
      <div className="flex items-center justify-between border-b border-line pb-2">
        <span className="font-semibold text-ink">Calibration &amp; Diagnostics</span>
        <span
          className={`rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider ${
            confidenceKind === "model"
              ? "bg-accent/15 text-accent border border-accent/30"
              : "bg-warn/15 text-warn border border-warn/30"
          }`}
        >
          {confidenceKind === "model" ? "Calibrated Model" : "Heuristic Plausibility"}
        </span>
      </div>

      {diagnostics && (
        <div className="grid grid-cols-2 gap-2 text-ink-muted">
          <div>
            <span className="text-ink-faint block text-[11px]">Image Sharpness</span>
            <span className="tabular font-mono text-ink">
              {diagnostics.sharpness_variance != null
                ? diagnostics.sharpness_variance.toFixed(1)
                : "N/A"}
              {diagnostics.is_blurry && (
                <span className="ml-1 text-warn font-sans text-[10px]">(Blurry)</span>
              )}
            </span>
          </div>
          <div>
            <span className="text-ink-faint block text-[11px]">RMS Contrast</span>
            <span className="tabular font-mono text-ink">
              {diagnostics.rms_contrast != null
                ? diagnostics.rms_contrast.toFixed(1)
                : "N/A"}
              {diagnostics.is_low_contrast && (
                <span className="ml-1 text-warn font-sans text-[10px]">(Low contrast)</span>
              )}
            </span>
          </div>
        </div>
      )}

      {selectedCrown && (
        <div className="border-t border-line/60 pt-2">
          <div className="text-[11px] text-ink-faint mb-1">Selected Crown Confidence</div>
          <div className="flex items-center justify-between">
            <span className="text-ink-muted">Raw score:</span>
            <span className="tabular font-mono text-ink">
              {raw != null ? `${(raw * 100).toFixed(1)}%` : "N/A"}
            </span>
          </div>
          {calibrated != null && (
            <div className="flex items-center justify-between mt-0.5">
              <span className="text-ink-muted">Calibrated (temp-scaled):</span>
              <span className="tabular font-mono text-accent">
                {(calibrated * 100).toFixed(1)}%
              </span>
            </div>
          )}

          {flags.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {flags.map((flag) => (
                <span
                  key={flag}
                  className={`rounded px-1.5 py-0.5 text-[10px] ${
                    flag === "human_review_recommended"
                      ? "bg-danger/20 text-danger border border-danger/40"
                      : "bg-surface-1 text-ink-muted border border-line"
                  }`}
                >
                  {flag.replace(/_/g, " ")}
                </span>
              ))}
            </div>
          )}

          {needsReview && (
            <p className="mt-2 text-[11px] leading-tight text-warn">
              High uncertainty detected. Crown boundary and detection should be verified with ground-truth reference.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
