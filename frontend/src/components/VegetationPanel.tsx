import { useState } from "react";
import { analyzeVegetation } from "../lib/api";
import type { Detection, SelectedFile, VegetationResponse } from "../types";

interface Props {
  currentImage: SelectedFile | null;
  currentDetections: Detection[];
}

const INDICES = [
  {
    id: "VARI",
    name: "VARI (Visible Atmospherically Resistant Index)",
    type: "RGB",
    bands: "Red, Green, Blue",
    formula: "(Green - Red) / (Green + Red - Blue)",
    description: "Estimates vegetation fraction and photosynthetic vigor using RGB channels with atmospheric resistance.",
  },
  {
    id: "GLI",
    name: "GLI (Green Leaf Index)",
    type: "RGB",
    bands: "Red, Green, Blue",
    formula: "(2*Green - Red - Blue) / (2*Green + Red + Blue)",
    description: "Standard RGB index sensitive to chlorophyll concentration and green leaf cover.",
  },
  {
    id: "ExG",
    name: "ExG (Excess Green Index)",
    type: "RGB",
    bands: "Red, Green, Blue",
    formula: "2*Green - Red - Blue",
    description: "High-contrast vegetative separation separating green foliage from soil/litter backgrounds.",
  },
  {
    id: "NDVI",
    name: "NDVI (Normalized Difference Vegetation Index)",
    type: "Multispectral",
    bands: "NIR, Red (Requires 4+ bands)",
    formula: "(NIR - Red) / (NIR + Red)",
    description: "Gold standard physiological vigor metric. Strictly requires calibrated Near-Infrared band.",
  },
  {
    id: "GNDVI",
    name: "GNDVI (Green Normalized Difference Vegetation Index)",
    type: "Multispectral",
    bands: "NIR, Green (Requires 4+ bands)",
    formula: "(NIR - Green) / (NIR + Green)",
    description: "High-sensitivity index for mature, dense canopies with high chlorophyll content.",
  },
  {
    id: "NDRE",
    name: "NDRE (Normalized Difference Red Edge)",
    type: "Multispectral",
    bands: "NIR, Red-Edge (Requires 5+ bands)",
    formula: "(NIR - RedEdge) / (NIR + RedEdge)",
    description: "Measures chlorophyll content in dense crops and multi-layered forest canopies.",
  },
  {
    id: "SAVI",
    name: "SAVI (Soil-Adjusted Vegetation Index)",
    type: "Multispectral",
    bands: "NIR, Red (Requires 4+ bands)",
    formula: "((NIR - Red) / (NIR + Red + 0.5)) * 1.5",
    description: "Minimizes soil brightness influences in sparse woodlands or arid savannah.",
  },
];

export function VegetationPanel({ currentImage, currentDetections }: Props) {
  const [selectedIndex, setSelectedIndex] = useState("VARI");
  const [customFile, setCustomFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<VegetationResponse | null>(null);

  const activeImage = customFile || currentImage?.file || null;
  const selectedIndexMeta = INDICES.find((i) => i.id === selectedIndex) || INDICES[0];

  const handleRunAnalysis = async () => {
    if (!activeImage) {
      setError("Please select or upload an image file first.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      // Serialize crown geometry if detections exist
      const crownsJson =
        currentDetections.length > 0 ? JSON.stringify(currentDetections) : undefined;
      const res = await analyzeVegetation(activeImage, selectedIndex, crownsJson);
      setResult(res);
    } catch (err: unknown) {
      setError((err as Error).message || "Vegetation index computation failed.");
    } finally {
      setLoading(false);
    }
  };

  const downloadCrownStatsCsv = () => {
    if (!result || result.crown_statistics.length === 0) return;
    const header = "crown_id,mean,median,min,max,std,status\n";
    const rows = result.crown_statistics
      .map(
        (c) =>
          `${c.crown_id},${c.mean ?? ""},${c.median ?? ""},${c.min ?? ""},${c.max ?? ""},${c.std ?? ""},${c.status ?? ""}`,
      )
      .join("\n");
    const blob = new Blob([header + rows], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `vegetation-${result.index_name}-crowns.csv`;
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
              Multispectral &amp; RGB Vegetation Health Analysis
            </h2>
            <span className="rounded bg-accent/15 px-2 py-0.5 text-[11px] font-mono text-accent">
              Module E
            </span>
          </div>
          <p className="text-[13px] text-ink-muted leading-relaxed">
            Per-crown zonal vegetation index calculation and scene-level canopy vigor modeling.
            Scientifically distinguishes native RGB indices (VARI, GLI, ExG) from calibrated NIR multispectral metrics.
          </p>
        </div>

        {/* Input Configuration Card */}
        <div className="rounded-lg border border-line bg-surface-1 p-5 shadow-sm">
          <h3 className="text-[15px] font-medium text-ink mb-3">Analysis Settings</h3>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-[13px]">
            {/* Image Selection */}
            <div>
              <label className="text-[11px] text-ink-faint uppercase tracking-wider block mb-1">
                Source Raster Imagery
              </label>
              <div className="flex flex-col gap-2">
                {currentImage?.file && (
                  <div className="flex items-center justify-between rounded border border-line bg-surface-2 p-2.5">
                    <span className="truncate text-ink font-mono text-[12px]">
                      {currentImage.file.name}
                    </span>
                    <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">
                      Active Image
                    </span>
                  </div>
                )}
                <div>
                  <label className="text-[11px] text-ink-muted block mb-1">
                    Or select another file (e.g. 4-band GeoTIFF):
                  </label>
                  <input
                    type="file"
                    accept=".tif,.tiff,.png,.jpg,.jpeg"
                    onChange={(e) => {
                      if (e.target.files && e.target.files[0]) {
                        setCustomFile(e.target.files[0]);
                      }
                    }}
                    className="w-full text-[12px] text-ink-muted file:mr-3 file:rounded file:border-0 file:bg-surface-2 file:px-3 file:py-1.5 file:text-[12px] file:text-ink hover:file:bg-surface-0 cursor-pointer"
                  />
                </div>
              </div>
            </div>

            {/* Index Selection */}
            <div>
              <label className="text-[11px] text-ink-faint uppercase tracking-wider block mb-1">
                Vegetation Index
              </label>
              <select
                value={selectedIndex}
                onChange={(e) => setSelectedIndex(e.target.value)}
                className="w-full rounded border border-line bg-surface-2 px-3 py-2 text-ink focus:border-accent focus:outline-none"
              >
                <optgroup label="RGB Indices (3-Band imagery compatible)">
                  <option value="VARI">VARI — Visible Atmospherically Resistant Index</option>
                  <option value="GLI">GLI — Green Leaf Index</option>
                  <option value="ExG">ExG — Excess Green Index</option>
                </optgroup>
                <optgroup label="Multispectral Indices (Requires 4+ bands with NIR)">
                  <option value="NDVI">NDVI — Normalized Difference Vegetation Index</option>
                  <option value="GNDVI">GNDVI — Green Normalized Difference</option>
                  <option value="NDRE">NDRE — Normalized Difference Red Edge</option>
                  <option value="SAVI">SAVI — Soil-Adjusted Vegetation Index</option>
                </optgroup>
              </select>

              <div className="mt-3 rounded border border-line/60 bg-surface-2/60 p-3 text-[12px] text-ink-muted">
                <div className="font-mono text-accent text-[11px] mb-1">
                  Formula: {selectedIndexMeta.formula}
                </div>
                <div className="text-[11px] text-ink-faint mb-1">
                  Bands required: {selectedIndexMeta.bands}
                </div>
                <div className="text-[11px] leading-relaxed">
                  {selectedIndexMeta.description}
                </div>
              </div>
            </div>
          </div>

          {/* Scientific Honesty Notice */}
          {selectedIndexMeta.type === "Multispectral" && (
            <div className="mt-4 rounded border border-warn/30 bg-warn/10 p-3 text-[12px] text-warn">
              <span className="font-medium">Scientific Rigor Notice:</span> Multispectral indices require a calibrated Near-Infrared (NIR) band (Band 4+). CanopyLens will not synthesize pseudo-NIR values for standard 3-band RGB rasters. For 3-band RGB imagery, please select VARI, GLI, or ExG.
            </div>
          )}

          <div className="mt-4 flex items-center justify-between border-t border-line pt-3">
            <span className="text-[12px] text-ink-muted">
              {currentDetections.length > 0
                ? `${currentDetections.length} crown polygons will be analyzed zonally.`
                : "No active crown detections; whole-scene statistics will be calculated."}
            </span>
            <button
              type="button"
              onClick={handleRunAnalysis}
              disabled={loading || !activeImage}
              className="rounded bg-accent px-4 py-2 font-medium text-surface-0 hover:bg-accent/90 disabled:opacity-50 transition-colors text-[13px]"
            >
              {loading ? "Computing Index…" : `Compute ${selectedIndex}`}
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded border border-danger/40 bg-danger/10 p-3 text-[13px] text-danger">
            {error}
          </div>
        )}

        {/* Results Card */}
        {result && (
          <div className="flex flex-col gap-6">
            {/* Scene Statistics */}
            <div className="rounded-lg border border-line bg-surface-1 p-5 shadow-sm">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-[15px] font-medium text-ink">
                  Scene Summary: {result.index_name}
                </h3>
                <span className="rounded bg-surface-2 border border-line px-2 py-0.5 text-[11px] font-mono text-ink-muted">
                  {result.band_count} bands detected
                </span>
              </div>

              <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-[12px]">
                <div className="rounded border border-line bg-surface-2 p-3">
                  <span className="text-ink-faint block text-[11px]">Mean Value</span>
                  <span className="tabular font-mono text-[16px] text-accent font-semibold">
                    {result.scene_statistics.mean != null
                      ? result.scene_statistics.mean.toFixed(3)
                      : "N/A"}
                  </span>
                </div>
                <div className="rounded border border-line bg-surface-2 p-3">
                  <span className="text-ink-faint block text-[11px]">Median Value</span>
                  <span className="tabular font-mono text-[16px] text-ink font-semibold">
                    {result.scene_statistics.median != null
                      ? result.scene_statistics.median.toFixed(3)
                      : "N/A"}
                  </span>
                </div>
                <div className="rounded border border-line bg-surface-2 p-3">
                  <span className="text-ink-faint block text-[11px]">Min / Max</span>
                  <span className="tabular font-mono text-[14px] text-ink">
                    {result.scene_statistics.min != null
                      ? result.scene_statistics.min.toFixed(2)
                      : "-"} / {result.scene_statistics.max != null
                      ? result.scene_statistics.max.toFixed(2)
                      : "-"}
                  </span>
                </div>
                <div className="rounded border border-line bg-surface-2 p-3">
                  <span className="text-ink-faint block text-[11px]">Std Deviation</span>
                  <span className="tabular font-mono text-[14px] text-ink">
                    {result.scene_statistics.std != null
                      ? result.scene_statistics.std.toFixed(3)
                      : "N/A"}
                  </span>
                </div>
                <div className="rounded border border-line bg-surface-2 p-3">
                  <span className="text-ink-faint block text-[11px]">Valid Pixels</span>
                  <span className="tabular font-mono text-[14px] text-ink">
                    {result.scene_statistics.valid_pixels.toLocaleString()}
                  </span>
                </div>
              </div>

              <p className="mt-3 text-[11px] text-ink-faint italic leading-relaxed">
                {result.notice}
              </p>
            </div>

            {/* Histogram / Distribution */}
            {result.distribution.length > 0 && (
              <div className="rounded-lg border border-line bg-surface-1 p-5 shadow-sm">
                <h3 className="text-[15px] font-medium text-ink mb-3">
                  Value Distribution Across Scene
                </h3>
                <div className="flex flex-col gap-1.5">
                  {(() => {
                    const maxCount = Math.max(...result.distribution.map((b) => b.count), 1);
                    return result.distribution.map((bin, i) => {
                      const pct = Math.round((bin.count / maxCount) * 100);
                      return (
                        <div key={i} className="flex items-center gap-3 text-[11px]">
                          <span className="w-24 font-mono text-ink-muted tabular">
                            [{bin.bin_start.toFixed(2)}, {bin.bin_end.toFixed(2)}]
                          </span>
                          <div className="flex-1 h-3 rounded bg-surface-2 overflow-hidden">
                            <div
                              className="h-full bg-accent/70 transition-all duration-300"
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                          <span className="w-16 text-right font-mono tabular text-ink-faint">
                            {bin.count.toLocaleString()}
                          </span>
                        </div>
                      );
                    });
                  })()}
                </div>
              </div>
            )}

            {/* Zonal Crown Health Statistics */}
            {result.crown_statistics.length > 0 && (
              <div className="rounded-lg border border-line bg-surface-1 p-5 shadow-sm">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <h3 className="text-[15px] font-medium text-ink">
                      Zonal Crown Health Statistics ({result.crown_statistics.length} crowns)
                    </h3>
                    <p className="text-[12px] text-ink-muted">
                      Individual canopy extraction statistics masked to crown boundary geometries.
                    </p>
                  </div>
                  <button
                    onClick={downloadCrownStatsCsv}
                    type="button"
                    className="rounded border border-line bg-surface-2 px-3 py-1.5 text-[12px] text-ink hover:bg-surface-0 transition-colors"
                  >
                    Export Crown Health CSV
                  </button>
                </div>

                <div className="max-h-80 overflow-y-auto">
                  <table className="w-full text-left text-[12px]">
                    <thead className="sticky top-0 bg-surface-1 border-b border-line text-ink-faint uppercase tracking-wider text-[10px]">
                      <tr>
                        <th className="py-2 px-3">Crown ID</th>
                        <th className="py-2 px-3">Mean</th>
                        <th className="py-2 px-3">Median</th>
                        <th className="py-2 px-3">Min / Max</th>
                        <th className="py-2 px-3">Std Dev</th>
                        <th className="py-2 px-3">Vigor Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line/40">
                      {result.crown_statistics.map((c) => (
                        <tr key={c.crown_id} className="hover:bg-surface-2/40">
                          <td className="py-2 px-3 font-mono text-ink-muted text-[11px]">
                            {c.crown_id}
                          </td>
                          <td className="py-2 px-3 tabular font-mono text-accent">
                            {c.mean != null ? c.mean.toFixed(3) : "-"}
                          </td>
                          <td className="py-2 px-3 tabular font-mono text-ink">
                            {c.median != null ? c.median.toFixed(3) : "-"}
                          </td>
                          <td className="py-2 px-3 tabular font-mono text-ink-muted">
                            {c.min != null ? c.min.toFixed(2) : "-"} /{" "}
                            {c.max != null ? c.max.toFixed(2) : "-"}
                          </td>
                          <td className="py-2 px-3 tabular font-mono text-ink-muted">
                            {c.std != null ? c.std.toFixed(3) : "-"}
                          </td>
                          <td className="py-2 px-3">
                            <span
                              className={`rounded px-1.5 py-0.5 text-[10px] uppercase font-mono ${
                                c.status === "vigorous"
                                  ? "bg-accent/20 text-accent"
                                  : c.status === "stressed"
                                  ? "bg-warn/20 text-warn"
                                  : c.status === "senescent"
                                  ? "bg-danger/20 text-danger"
                                  : "bg-surface-2 text-ink-muted"
                              }`}
                            >
                              {c.status ?? "unknown"}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
