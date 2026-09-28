import { useEffect, useState } from "react";
import {
  evaluateModel,
  fetchBenchmarks,
  fetchModels,
  fetchTrainJob,
  startTraining,
} from "../lib/api";
import type {
  BenchmarkResponse,
  EvaluationResponse,
  ModelMetadata,
  TrainJobStatus,
} from "../types";

export function ModelEvaluationDashboard() {
  const [models, setModels] = useState<ModelMetadata[]>([]);
  const [benchmarks, setBenchmarks] = useState<BenchmarkResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Evaluation state
  const [selectedModel, setSelectedModel] = useState<string>("deepforest_neon");
  const [selectedDataset, setSelectedDataset] = useState<string>("neon_eval");
  const [evaluating, setEvaluating] = useState(false);
  const [evalResult, setEvalResult] = useState<EvaluationResponse | null>(null);
  const [evalError, setEvalError] = useState<string | null>(null);

  // Training state
  const [trainModelName, setTrainModelName] = useState("forest_finetuned_v1");
  const [trainBaseModel, setTrainBaseModel] = useState("deepforest_neon");
  const [trainEpochs, setTrainEpochs] = useState(5);
  const [trainBatchSize, setTrainBatchSize] = useState(4);
  const [trainLr, setTrainLr] = useState(0.0001);
  const [trainJob, setTrainJob] = useState<TrainJobStatus | null>(null);
  const [trainStarting, setTrainStarting] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([fetchModels(), fetchBenchmarks()])
      .then(([modelsData, benchmarkData]) => {
        if (!active) return;
        setModels(modelsData);
        setBenchmarks(benchmarkData);
        if (modelsData.length > 0) {
          setSelectedModel(modelsData[0].name);
        }
        setLoading(false);
      })
      .catch((err: Error) => {
        if (!active) return;
        setError(err.message || "Failed to load model registry.");
        setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  // Poll training job if active
  useEffect(() => {
    if (!trainJob || trainJob.status === "completed" || trainJob.status === "failed") {
      return;
    }
    const interval = window.setInterval(() => {
      fetchTrainJob(trainJob.job_id)
        .then((status) => {
          setTrainJob(status);
          if (status.status === "completed") {
            // refresh models
            void fetchModels().then(setModels);
            void fetchBenchmarks().then(setBenchmarks);
          }
        })
        .catch(() => {});
    }, 1500);

    return () => clearInterval(interval);
  }, [trainJob]);

  const handleRunEvaluation = async () => {
    setEvaluating(true);
    setEvalError(null);
    try {
      const res = await evaluateModel(selectedModel, selectedDataset);
      setEvalResult(res);
      // Refresh benchmarks
      const bRes = await fetchBenchmarks();
      setBenchmarks(bRes);
    } catch (err: unknown) {
      setEvalError((err as Error).message || "Evaluation failed");
    } finally {
      setEvaluating(false);
    }
  };

  const handleStartTraining = async (e: React.FormEvent) => {
    e.preventDefault();
    setTrainStarting(true);
    try {
      const accepted = await startTraining({
        model_name: trainModelName,
        base_model: trainBaseModel,
        epochs: trainEpochs,
        batch_size: trainBatchSize,
        lr: trainLr,
      });
      const initialStatus = await fetchTrainJob(accepted.job_id);
      setTrainJob(initialStatus);
    } catch (err: unknown) {
      alert(`Could not start training: ${(err as Error).message}`);
    } finally {
      setTrainStarting(false);
    }
  };

  const copyMarkdown = () => {
    if (!benchmarks?.markdown_table) return;
    navigator.clipboard.writeText(benchmarks.markdown_table);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-ink-muted">
        <span className="text-[13px]">Loading model intelligence data…</span>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-surface-0 p-6 text-ink">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
        {/* Header */}
        <div className="flex flex-col gap-1 border-b border-line pb-4">
          <div className="flex items-center justify-between">
            <h2 className="text-[20px] font-semibold tracking-tight text-ink">
              Model Registry &amp; Evaluation Benchmarks
            </h2>
            <div className="flex items-center gap-2">
              <span className="rounded bg-accent/15 px-2 py-0.5 text-[11px] font-mono text-accent">
                {models.length} registered models
              </span>
            </div>
          </div>
          <p className="text-[13px] text-ink-muted leading-relaxed">
            Standardized evaluation framework with multi-threshold IoU matching (0.50 to 0.95), count error (MAE/RMSE),
            and forest-type breakdown with strict spatial block partitioning to eliminate spatial autocorrelation data leakage.
          </p>
        </div>

        {error && (
          <div className="rounded border border-danger/40 bg-danger/10 p-3 text-[13px] text-danger">
            {error}
          </div>
        )}

        {/* Section 1: Benchmark Comparison Table */}
        <div className="rounded-lg border border-line bg-surface-1 p-5 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-[15px] font-medium text-ink">Standardized Model Benchmarks</h3>
              <p className="text-[12px] text-ink-muted">
                COCO-compliant detection metrics evaluated on identical test splits.
              </p>
            </div>
            {benchmarks?.markdown_table && (
              <button
                onClick={copyMarkdown}
                type="button"
                className="rounded border border-line bg-surface-2 px-3 py-1.5 text-[12px] text-ink hover:bg-surface-0 transition-colors"
              >
                {copied ? "Copied Markdown!" : "Copy Markdown Table"}
              </button>
            )}
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-[12px]">
              <thead className="border-b border-line text-ink-faint uppercase tracking-wider text-[10px]">
                <tr>
                  <th className="py-2 px-3">Model</th>
                  <th className="py-2 px-3">Architecture</th>
                  <th className="py-2 px-3">mAP@50</th>
                  <th className="py-2 px-3">mAP@50:95</th>
                  <th className="py-2 px-3">Precision@50</th>
                  <th className="py-2 px-3">Recall@50</th>
                  <th className="py-2 px-3">F1@50</th>
                  <th className="py-2 px-3">Count MAE</th>
                  <th className="py-2 px-3">Count RMSE</th>
                  <th className="py-2 px-3">Area Ratio</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line/40 text-ink">
                {benchmarks && benchmarks.comparison_table.length > 0 ? (
                  benchmarks.comparison_table.map((b) => {
                    const mMeta = models.find((m) => m.name === b.model_name);
                    return (
                      <tr key={b.model_name} className="hover:bg-surface-2/40">
                        <td className="py-2.5 px-3 font-medium text-ink flex items-center gap-1.5">
                          {b.model_name}
                          {mMeta?.is_default && (
                            <span className="rounded bg-accent/20 px-1 py-0.2 text-[9px] text-accent">
                              default
                            </span>
                          )}
                          {mMeta?.is_fallback && (
                            <span className="rounded bg-warn/20 px-1 py-0.2 text-[9px] text-warn">
                              fallback
                            </span>
                          )}
                        </td>
                        <td className="py-2.5 px-3 text-ink-muted">
                          {mMeta?.architecture || "classical"}
                        </td>
                        <td className="py-2.5 px-3 tabular font-mono text-accent">
                          {(b.map50 * 100).toFixed(1)}%
                        </td>
                        <td className="py-2.5 px-3 tabular font-mono text-ink">
                          {(b.map50_95 * 100).toFixed(1)}%
                        </td>
                        <td className="py-2.5 px-3 tabular font-mono">
                          {(b.precision_50 * 100).toFixed(1)}%
                        </td>
                        <td className="py-2.5 px-3 tabular font-mono">
                          {(b.recall_50 * 100).toFixed(1)}%
                        </td>
                        <td className="py-2.5 px-3 tabular font-mono">
                          {(b.f1_50 * 100).toFixed(1)}%
                        </td>
                        <td className="py-2.5 px-3 tabular font-mono text-ink-muted">
                          {b.count_mae.toFixed(1)}
                        </td>
                        <td className="py-2.5 px-3 tabular font-mono text-ink-muted">
                          {b.count_rmse.toFixed(1)}
                        </td>
                        <td className="py-2.5 px-3 tabular font-mono text-ink-muted">
                          {b.area_ratio.toFixed(2)}
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={10} className="py-4 text-center text-ink-faint">
                      No benchmark metrics recorded yet. Run an evaluation below.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Section 2: Two-column grid: Model Evaluator & Fine-Tuning */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Column A: Model Evaluator */}
          <div className="flex flex-col gap-4 rounded-lg border border-line bg-surface-1 p-5 shadow-sm">
            <div>
              <h3 className="text-[15px] font-medium text-ink">Run Ground-Truth Evaluation</h3>
              <p className="text-[12px] text-ink-muted">
                Evaluate a model against ground truth annotations with forest-type stratification.
              </p>
            </div>

            <div className="flex flex-col gap-3 text-[13px]">
              <div>
                <label className="text-[11px] text-ink-faint uppercase tracking-wider block mb-1">
                  Model
                </label>
                <select
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  className="w-full rounded border border-line bg-surface-2 px-3 py-2 text-ink focus:border-accent focus:outline-none"
                >
                  {models.map((m) => (
                    <option key={m.id} value={m.name}>
                      {m.name} ({m.architecture})
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="text-[11px] text-ink-faint uppercase tracking-wider block mb-1">
                  Evaluation Dataset
                </label>
                <select
                  value={selectedDataset}
                  onChange={(e) => setSelectedDataset(e.target.value)}
                  className="w-full rounded border border-line bg-surface-2 px-3 py-2 text-ink focus:border-accent focus:outline-none"
                >
                  <option value="neon_eval">NEON Tree Crown Benchmark Split</option>
                  <option value="tropical_canopy">Tropical Dense Canopy Split</option>
                  <option value="boreal_conifer">Boreal Conifer Plantation Split</option>
                </select>
              </div>

              <button
                type="button"
                onClick={handleRunEvaluation}
                disabled={evaluating}
                className="mt-2 rounded bg-accent px-4 py-2 font-medium text-surface-0 hover:bg-accent/90 disabled:opacity-50 transition-colors"
              >
                {evaluating ? "Evaluating Dataset…" : "Run Evaluation"}
              </button>

              {evalError && (
                <div className="rounded border border-danger/40 bg-danger/10 p-2 text-[12px] text-danger">
                  {evalError}
                </div>
              )}

              {evalResult && (
                <div className="mt-2 rounded border border-line bg-surface-2 p-3 text-[12px] flex flex-col gap-2">
                  <div className="font-medium text-ink border-b border-line pb-1">
                    Evaluation Result: {evalResult.model_name}
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-ink-muted">
                    <div>
                      AP@50:{" "}
                      <span className="tabular font-mono text-accent">
                        {((evalResult.metrics_at_50.average_precision || 0) * 100).toFixed(1)}%
                      </span>
                    </div>
                    <div>
                      F1 Score:{" "}
                      <span className="tabular font-mono text-ink">
                        {((evalResult.metrics_at_50.f1_score || 0) * 100).toFixed(1)}%
                      </span>
                    </div>
                    <div>
                      Precision:{" "}
                      <span className="tabular font-mono text-ink">
                        {((evalResult.metrics_at_50.precision || 0) * 100).toFixed(1)}%
                      </span>
                    </div>
                    <div>
                      Recall:{" "}
                      <span className="tabular font-mono text-ink">
                        {((evalResult.metrics_at_50.recall || 0) * 100).toFixed(1)}%
                      </span>
                    </div>
                    <div>
                      Count MAE:{" "}
                      <span className="tabular font-mono text-ink">
                        {evalResult.metrics_at_50.count_mae?.toFixed(1) ?? "N/A"}
                      </span>
                    </div>
                    <div>
                      Count RMSE:{" "}
                      <span className="tabular font-mono text-ink">
                        {evalResult.metrics_at_50.count_rmse?.toFixed(1) ?? "N/A"}
                      </span>
                    </div>
                  </div>

                  {Object.keys(evalResult.forest_types).length > 0 && (
                    <div className="mt-2 border-t border-line/60 pt-2">
                      <span className="text-[11px] text-ink-faint block mb-1">
                        Forest-Type Breakdown
                      </span>
                      <div className="flex flex-col gap-1 text-[11px]">
                        {Object.entries(evalResult.forest_types).map(([ft, data]) => (
                          <div key={ft} className="flex justify-between text-ink-muted">
                            <span className="capitalize">{ft.replace(/_/g, " ")}:</span>
                            <span className="tabular font-mono text-ink">
                              F1: {(data.f1 * 100).toFixed(0)}% (GT: {data.ground_truth_count})
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Column B: Domain-Specific Fine-Tuning */}
          <div className="flex flex-col gap-4 rounded-lg border border-line bg-surface-1 p-5 shadow-sm">
            <div>
              <h3 className="text-[15px] font-medium text-ink">Domain-Specific Fine-Tuning</h3>
              <p className="text-[12px] text-ink-muted">
                Adapt RetinaNet / DeepForest backbones to regional forest structures with early stopping.
              </p>
            </div>

            <form onSubmit={handleStartTraining} className="flex flex-col gap-3 text-[13px]">
              <div>
                <label className="text-[11px] text-ink-faint uppercase tracking-wider block mb-1">
                  New Model Name
                </label>
                <input
                  type="text"
                  value={trainModelName}
                  onChange={(e) => setTrainModelName(e.target.value)}
                  required
                  className="w-full rounded border border-line bg-surface-2 px-3 py-2 text-ink focus:border-accent focus:outline-none"
                />
              </div>

              <div>
                <label className="text-[11px] text-ink-faint uppercase tracking-wider block mb-1">
                  Base Model Weights
                </label>
                <select
                  value={trainBaseModel}
                  onChange={(e) => setTrainBaseModel(e.target.value)}
                  className="w-full rounded border border-line bg-surface-2 px-3 py-2 text-ink focus:border-accent focus:outline-none"
                >
                  <option value="deepforest_neon">DeepForest NEON Benchmark (RetinaNet)</option>
                  <option value="classical_baseline">Classical Baseline (Morphological)</option>
                </select>
              </div>

              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="text-[11px] text-ink-faint uppercase tracking-wider block mb-1">
                    Epochs
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={trainEpochs}
                    onChange={(e) => setTrainEpochs(Number(e.target.value))}
                    className="w-full rounded border border-line bg-surface-2 px-3 py-2 text-ink focus:border-accent focus:outline-none"
                  />
                </div>
                <div>
                  <label className="text-[11px] text-ink-faint uppercase tracking-wider block mb-1">
                    Batch Size
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={16}
                    value={trainBatchSize}
                    onChange={(e) => setTrainBatchSize(Number(e.target.value))}
                    className="w-full rounded border border-line bg-surface-2 px-3 py-2 text-ink focus:border-accent focus:outline-none"
                  />
                </div>
                <div>
                  <label className="text-[11px] text-ink-faint uppercase tracking-wider block mb-1">
                    Learning Rate
                  </label>
                  <input
                    type="number"
                    step="0.00001"
                    value={trainLr}
                    onChange={(e) => setTrainLr(Number(e.target.value))}
                    className="w-full rounded border border-line bg-surface-2 px-3 py-2 text-ink focus:border-accent focus:outline-none"
                  />
                </div>
              </div>

              <button
                type="submit"
                disabled={trainStarting || (trainJob != null && trainJob.status === "running")}
                className="mt-2 rounded bg-surface-2 border border-line px-4 py-2 font-medium text-ink hover:bg-surface-0 hover:border-line-strong disabled:opacity-50 transition-colors"
              >
                {trainStarting ? "Initializing Training…" : "Launch Fine-Tuning Job"}
              </button>

              {trainJob && (
                <div className="mt-2 rounded border border-line bg-surface-2 p-3 text-[12px] flex flex-col gap-2">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-ink">{trainJob.model_name}</span>
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] uppercase font-mono ${
                        trainJob.status === "completed"
                          ? "bg-accent/20 text-accent"
                          : trainJob.status === "failed"
                          ? "bg-danger/20 text-danger"
                          : "bg-warn/20 text-warn"
                      }`}
                    >
                      {trainJob.status}
                    </span>
                  </div>

                  {/* Progress bar */}
                  <div className="h-2 w-full rounded bg-surface-0 overflow-hidden">
                    <div
                      className="h-full bg-accent transition-all duration-300"
                      style={{ width: `${Math.round(trainJob.progress * 100)}%` }}
                    />
                  </div>

                  <div className="flex justify-between text-ink-muted text-[11px]">
                    <span>
                      Epoch {trainJob.epoch} / {trainJob.total_epochs}
                    </span>
                    <span>Loss: {trainJob.loss.toFixed(4)}</span>
                    <span>{Math.round(trainJob.progress * 100)}%</span>
                  </div>

                  {trainJob.status === "completed" && (
                    <div className="text-[11px] text-accent mt-1">
                      Model weights registered to registry and ready for deployment.
                    </div>
                  )}
                </div>
              )}
            </form>
          </div>
        </div>

        {/* Section 3: Model Catalog */}
        <div className="rounded-lg border border-line bg-surface-1 p-5 shadow-sm">
          <h3 className="text-[15px] font-medium text-ink mb-3">Model Registry Catalog</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {models.map((m) => (
              <div
                key={m.id}
                className="flex flex-col justify-between rounded border border-line bg-surface-2 p-4 text-[12px]"
              >
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <span className="font-semibold text-ink text-[13px]">{m.name}</span>
                    <span className="text-[10px] font-mono text-ink-faint">v{m.version}</span>
                  </div>
                  <div className="text-[11px] font-mono text-accent mb-2">
                    {m.architecture}
                  </div>
                  <p className="text-ink-muted text-[11px] line-clamp-3 mb-3 leading-relaxed">
                    {m.description}
                  </p>
                </div>

                <div className="border-t border-line/60 pt-2 flex flex-col gap-1 text-[11px] text-ink-faint">
                  <div className="flex justify-between">
                    <span>Resolution:</span>
                    <span className="text-ink">
                      {m.image_resolution_m ? `${m.image_resolution_m} m/px` : "Multi-res"}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span>Status:</span>
                    <span className="text-ink capitalize">{m.status}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Training set:</span>
                    <span className="text-ink">{m.dataset_name || "Pretrained"}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
