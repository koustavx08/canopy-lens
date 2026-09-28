import { useState } from "react";
import { analyzeTemporalChange } from "../lib/api";
import { formatOptionalArea } from "../lib/format";
import type { Detection, TemporalChangeResponse } from "../types";

interface Props {
  currentDetections: Detection[];
}

export function TemporalChangePanel({ currentDetections }: Props) {
  const [t1Detections, setT1Detections] = useState<unknown[]>([]);
  const [t2Detections, setT2Detections] = useState<unknown[]>([]);
  const [t1Label, setT1Label] = useState<string>("T1 Baseline");
  const [t2Label, setT2Label] = useState<string>("T2 Monitoring");
  const [iouThreshold, setIouThreshold] = useState<number>(0.3);
  const [areaDiffThreshold, setAreaDiffThreshold] = useState<number>(0.2);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TemporalChangeResponse | null>(null);

  const handleT1FileUpload = (file: File) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const parsed = JSON.parse(e.target?.result as string);
        const dets = Array.isArray(parsed) ? parsed : (parsed.detections || parsed.features || []);
        setT1Detections(dets);
        setT1Label(file.name);
      } catch {
        setError(`Failed to parse ${file.name} as JSON.`);
      }
    };
    reader.readAsText(file);
  };

  const handleT2FileUpload = (file: File) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const parsed = JSON.parse(e.target?.result as string);
        const dets = Array.isArray(parsed) ? parsed : (parsed.detections || parsed.features || []);
        setT2Detections(dets);
        setT2Label(file.name);
      } catch {
        setError(`Failed to parse ${file.name} as JSON.`);
      }
    };
    reader.readAsText(file);
  };

  const useCurrentAsT1 = () => {
    if (currentDetections.length === 0) return;
    setT1Detections(currentDetections);
    setT1Label(`Active Survey (${currentDetections.length} crowns)`);
  };

  const useCurrentAsT2 = () => {
    if (currentDetections.length === 0) return;
    setT2Detections(currentDetections);
    setT2Label(`Active Survey (${currentDetections.length} crowns)`);
  };

  const handleRunChangeDetection = async () => {
    if (t1Detections.length === 0 || t2Detections.length === 0) {
      setError("Both T1 and T2 crown sets are required to compute canopy change.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await analyzeTemporalChange(
        t1Detections,
        t2Detections,
        iouThreshold,
        areaDiffThreshold,
      );
      setResult(res);
    } catch (err: unknown) {
      setError((err as Error).message || "Canopy change detection failed.");
    } finally {
      setLoading(false);
    }
  };

  const downloadGeoJson = () => {
    if (!result?.change_geojson) return;
    const blob = new Blob([JSON.stringify(result.change_geojson, null, 2)], {
      type: "application/geo+json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "canopy-temporal-change.geojson";
    a.click();
    URL.revokeObjectURL(url);
  };

  const downloadCsv = () => {
    if (!result || result.crown_changes.length === 0) return;
    const header = "type,crown_t1_id,crown_t2_id,iou,area_t1_m2,area_t2_m2,area_diff_m2\n";
    const rows = result.crown_changes
      .map(
        (c) =>
          `${c.type},${c.crown_t1_id ?? ""},${c.crown_t2_id ?? ""},${c.iou.toFixed(3)},${
            c.area_t1_m2 ?? ""
          },${c.area_t2_m2 ?? ""},${c.area_diff_m2 ?? ""}`,
      )
      .join("\n");
    const blob = new Blob([header + rows], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "canopy-temporal-change.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-surface-0 p-6 text-ink">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-6">
        {/* Header */}
        <div className="flex flex-col gap-1 border-b border-line pb-4">
          <div className="flex items-center justify-between">
            <h2 className="text-[20px] font-semibold tracking-tight text-ink">
              Multi-Temporal Canopy Change Detection
            </h2>
            <span className="rounded bg-accent/15 px-2 py-0.5 text-[11px] font-mono text-accent">
              Module F
            </span>
          </div>
          <p className="text-[13px] text-ink-muted leading-relaxed">
            Bi-temporal crown tracking identifying persistent, lost (mortality / harvest), recruited (growth / reforestation),
            and disturbed canopy structures across survey epochs.
          </p>
        </div>

        {/* Input Card */}
        <div className="rounded-lg border border-line bg-surface-1 p-5 shadow-sm">
          <h3 className="text-[15px] font-medium text-ink mb-3">Epoch Configuration</h3>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 text-[13px]">
            {/* T1 Baseline */}
            <div className="rounded border border-line bg-surface-2 p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="font-medium text-ink">T1: Baseline Epoch</span>
                <span className="text-[11px] font-mono text-ink-faint">
                  {t1Detections.length} crowns
                </span>
              </div>
              <p className="text-[11px] text-ink-muted mb-3">
                Source: {t1Label}
              </p>
              <div className="flex flex-col gap-2">
                {currentDetections.length > 0 && (
                  <button
                    type="button"
                    onClick={useCurrentAsT1}
                    className="rounded border border-line bg-surface-1 px-3 py-1.5 text-[12px] text-ink hover:bg-surface-0 transition-colors"
                  >
                    Use Active Survey Crowns ({currentDetections.length})
                  </button>
                )}
                <div>
                  <label className="text-[10px] text-ink-faint uppercase tracking-wider block mb-1">
                    Or upload GeoJSON / CSV detections:
                  </label>
                  <input
                    type="file"
                    accept=".json,.geojson"
                    onChange={(e) => {
                      if (e.target.files && e.target.files[0]) {
                        handleT1FileUpload(e.target.files[0]);
                      }
                    }}
                    className="w-full text-[11px] text-ink-muted file:mr-2 file:rounded file:border-0 file:bg-surface-1 file:px-2.5 file:py-1 file:text-[11px] file:text-ink cursor-pointer"
                  />
                </div>
              </div>
            </div>

            {/* T2 Monitoring */}
            <div className="rounded border border-line bg-surface-2 p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="font-medium text-ink">T2: Monitoring Epoch</span>
                <span className="text-[11px] font-mono text-ink-faint">
                  {t2Detections.length} crowns
                </span>
              </div>
              <p className="text-[11px] text-ink-muted mb-3">
                Source: {t2Label}
              </p>
              <div className="flex flex-col gap-2">
                {currentDetections.length > 0 && (
                  <button
                    type="button"
                    onClick={useCurrentAsT2}
                    className="rounded border border-line bg-surface-1 px-3 py-1.5 text-[12px] text-ink hover:bg-surface-0 transition-colors"
                  >
                    Use Active Survey Crowns ({currentDetections.length})
                  </button>
                )}
                <div>
                  <label className="text-[10px] text-ink-faint uppercase tracking-wider block mb-1">
                    Or upload GeoJSON / CSV detections:
                  </label>
                  <input
                    type="file"
                    accept=".json,.geojson"
                    onChange={(e) => {
                      if (e.target.files && e.target.files[0]) {
                        handleT2FileUpload(e.target.files[0]);
                      }
                    }}
                    className="w-full text-[11px] text-ink-muted file:mr-2 file:rounded file:border-0 file:bg-surface-1 file:px-2.5 file:py-1 file:text-[11px] file:text-ink cursor-pointer"
                  />
                </div>
              </div>
            </div>
          </div>

          {/* Thresholds */}
          <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4 border-t border-line pt-4 text-[12px]">
            <div>
              <div className="flex justify-between mb-1">
                <span className="text-ink">Temporal IoU Matching Threshold</span>
                <span className="tabular font-mono text-accent">{iouThreshold.toFixed(2)}</span>
              </div>
              <input
                type="range"
                min={0.1}
                max={0.8}
                step={0.05}
                value={iouThreshold}
                onChange={(e) => setIouThreshold(Number(e.target.value))}
                className="w-full accent-accent cursor-pointer"
              />
              <span className="text-[10px] text-ink-faint block mt-0.5">
                Minimum bounding IoU to match the same tree crown across time.
              </span>
            </div>

            <div>
              <div className="flex justify-between mb-1">
                <span className="text-ink">Crown Area Disturbance Threshold</span>
                <span className="tabular font-mono text-accent">
                  {Math.round(areaDiffThreshold * 100)}%
                </span>
              </div>
              <input
                type="range"
                min={0.05}
                max={0.5}
                step={0.05}
                value={areaDiffThreshold}
                onChange={(e) => setAreaDiffThreshold(Number(e.target.value))}
                className="w-full accent-accent cursor-pointer"
              />
              <span className="text-[10px] text-ink-faint block mt-0.5">
                Fractional area change before a crown is flagged as disturbed.
              </span>
            </div>
          </div>

          <div className="mt-5 flex items-center justify-between border-t border-line pt-3">
            <span className="text-[12px] text-ink-muted">
              Ready to match {t1Detections.length} baseline crowns against {t2Detections.length} monitoring crowns.
            </span>
            <button
              type="button"
              onClick={handleRunChangeDetection}
              disabled={loading || t1Detections.length === 0 || t2Detections.length === 0}
              className="rounded bg-accent px-4 py-2 font-medium text-surface-0 hover:bg-accent/90 disabled:opacity-50 transition-colors text-[13px]"
            >
              {loading ? "Matching Epochs…" : "Detect Canopy Change"}
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded border border-danger/40 bg-danger/10 p-3 text-[13px] text-danger">
            {error}
          </div>
        )}

        {/* Change Results */}
        {result && (
          <div className="flex flex-col gap-6">
            {/* Summary Metrics */}
            <div className="rounded-lg border border-line bg-surface-1 p-5 shadow-sm">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-[15px] font-medium text-ink">Canopy Dynamic Summary</h3>
                <div className="flex items-center gap-2">
                  <button
                    onClick={downloadGeoJson}
                    type="button"
                    className="rounded border border-line bg-surface-2 px-3 py-1.5 text-[12px] text-ink hover:bg-surface-0 transition-colors"
                  >
                    Export Change GeoJSON
                  </button>
                  <button
                    onClick={downloadCsv}
                    type="button"
                    className="rounded border border-line bg-surface-2 px-3 py-1.5 text-[12px] text-ink hover:bg-surface-0 transition-colors"
                  >
                    Export Change CSV
                  </button>
                </div>
              </div>

              {/* Cards Grid */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-[12px]">
                <div className="rounded border border-line bg-surface-2 p-3">
                  <span className="text-ink-faint block text-[11px]">Persistent Crowns</span>
                  <span className="tabular font-mono text-[18px] text-ink font-semibold">
                    {result.summary.persistent_crowns.toLocaleString()}
                  </span>
                  <span className="block text-[10px] text-ink-muted mt-0.5">
                    {(result.summary.canopy_persistence_rate * 100).toFixed(1)}% persistence
                  </span>
                </div>

                <div className="rounded border border-danger/30 bg-danger/10 p-3">
                  <span className="text-danger/80 block text-[11px]">Lost Crowns</span>
                  <span className="tabular font-mono text-[18px] text-danger font-semibold">
                    {result.summary.lost_crowns.toLocaleString()}
                  </span>
                  <span className="block text-[10px] text-danger/80 mt-0.5">
                    Mortality / Harvest
                  </span>
                </div>

                <div className="rounded border border-accent/30 bg-accent/10 p-3">
                  <span className="text-accent/80 block text-[11px]">Recruited Crowns</span>
                  <span className="tabular font-mono text-[18px] text-accent font-semibold">
                    {result.summary.recruited_crowns.toLocaleString()}
                  </span>
                  <span className="block text-[10px] text-accent/80 mt-0.5">
                    Growth / New Planting
                  </span>
                </div>

                <div className="rounded border border-warn/30 bg-warn/10 p-3">
                  <span className="text-warn/80 block text-[11px]">Disturbed Crowns</span>
                  <span className="tabular font-mono text-[18px] text-warn font-semibold">
                    {result.summary.disturbed_crowns.toLocaleString()}
                  </span>
                  <span className="block text-[10px] text-warn/80 mt-0.5">
                    Partial Damage / Vigorous Shift
                  </span>
                </div>
              </div>

              {/* Area figures */}
              <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-3 border-t border-line pt-3 text-[12px]">
                <div>
                  <span className="text-ink-faint block text-[11px]">Canopy Gain</span>
                  <span className="tabular font-mono text-accent font-medium">
                    {formatOptionalArea(result.summary.canopy_gain_m2) ?? `${result.summary.canopy_gain_m2.toFixed(1)} m²`}
                  </span>
                </div>
                <div>
                  <span className="text-ink-faint block text-[11px]">Canopy Loss</span>
                  <span className="tabular font-mono text-danger font-medium">
                    {formatOptionalArea(result.summary.canopy_loss_m2) ?? `${result.summary.canopy_loss_m2.toFixed(1)} m²`}
                  </span>
                </div>
                <div>
                  <span className="text-ink-faint block text-[11px]">Net Change</span>
                  <span
                    className={`tabular font-mono font-medium ${
                      result.summary.net_canopy_change_m2 >= 0 ? "text-accent" : "text-danger"
                    }`}
                  >
                    {result.summary.net_canopy_change_m2 >= 0 ? "+" : ""}
                    {formatOptionalArea(result.summary.net_canopy_change_m2) ?? `${result.summary.net_canopy_change_m2.toFixed(1)} m²`}
                  </span>
                </div>
              </div>

              <p className="mt-3 text-[11px] text-ink-faint italic leading-relaxed">
                {result.notice}
              </p>
            </div>

            {/* Crown Event Table */}
            <div className="rounded-lg border border-line bg-surface-1 p-5 shadow-sm">
              <h3 className="text-[15px] font-medium text-ink mb-3">
                Detected Crown Change Events ({result.crown_changes.length})
              </h3>

              <div className="max-h-80 overflow-y-auto">
                <table className="w-full text-left text-[12px]">
                  <thead className="sticky top-0 bg-surface-1 border-b border-line text-ink-faint uppercase tracking-wider text-[10px]">
                    <tr>
                      <th className="py-2 px-3">Dynamic Class</th>
                      <th className="py-2 px-3">T1 ID</th>
                      <th className="py-2 px-3">T2 ID</th>
                      <th className="py-2 px-3">IoU Overlap</th>
                      <th className="py-2 px-3">T1 Area</th>
                      <th className="py-2 px-3">T2 Area</th>
                      <th className="py-2 px-3">Area Diff</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line/40">
                    {result.crown_changes.map((c, i) => (
                      <tr key={i} className="hover:bg-surface-2/40">
                        <td className="py-2 px-3">
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] uppercase font-mono ${
                              c.type === "lost"
                                ? "bg-danger/20 text-danger border border-danger/30"
                                : c.type === "recruited"
                                ? "bg-accent/20 text-accent border border-accent/30"
                                : c.type === "disturbed"
                                ? "bg-warn/20 text-warn border border-warn/30"
                                : "bg-surface-2 text-ink-muted border border-line"
                            }`}
                          >
                            {c.type}
                          </span>
                        </td>
                        <td className="py-2 px-3 font-mono text-ink-muted text-[11px]">
                          {c.crown_t1_id ?? "-"}
                        </td>
                        <td className="py-2 px-3 font-mono text-ink-muted text-[11px]">
                          {c.crown_t2_id ?? "-"}
                        </td>
                        <td className="py-2 px-3 tabular font-mono text-ink">
                          {c.iou > 0 ? c.iou.toFixed(3) : "-"}
                        </td>
                        <td className="py-2 px-3 tabular font-mono text-ink-muted">
                          {c.area_t1_m2 != null ? `${c.area_t1_m2.toFixed(1)} m²` : "-"}
                        </td>
                        <td className="py-2 px-3 tabular font-mono text-ink-muted">
                          {c.area_t2_m2 != null ? `${c.area_t2_m2.toFixed(1)} m²` : "-"}
                        </td>
                        <td className="py-2 px-3 tabular font-mono">
                          {c.area_diff_m2 != null ? (
                            <span
                              className={
                                c.area_diff_m2 > 0
                                  ? "text-accent"
                                  : c.area_diff_m2 < 0
                                  ? "text-danger"
                                  : "text-ink"
                              }
                            >
                              {c.area_diff_m2 > 0 ? "+" : ""}
                              {c.area_diff_m2.toFixed(1)} m²
                            </span>
                          ) : (
                            "-"
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
