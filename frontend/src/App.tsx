import { useCallback, useEffect, useMemo, useState } from "react";
import { Header } from "./components/Header";
import { MapView } from "./components/MapView";
import { ResultsPanel } from "./components/ResultsPanel";
import { Sidebar } from "./components/Sidebar";
import { ApiError, fetchCapabilities, previewBoundary } from "./lib/api";
import { useAnalysis } from "./lib/useAnalysis";
import type {
  BoundaryPreview,
  Capabilities,
  DetectionSettings,
  SelectedFile,
} from "./types";

const PREVIEWABLE = /\.(png|jpe?g|webp|gif)$/i;

export default function App() {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [image, setImage] = useState<SelectedFile | null>(null);
  const [boundary, setBoundary] = useState<File | null>(null);
  const [boundaryPreview, setBoundaryPreview] = useState<BoundaryPreview | null>(null);
  const [boundaryError, setBoundaryError] = useState<string | null>(null);
  const [boundaryLoading, setBoundaryLoading] = useState(false);
  const [selectedCrown, setSelectedCrown] = useState<string | null>(null);
  const [settings, setSettings] = useState<DetectionSettings>({
    confidenceThreshold: 0.3,
    minCrownAreaM2: 1,
    engine: "auto",
  });

  const analysis = useAnalysis();
  const { run, reset } = analysis;

  // Load server capabilities once; the defaults for the controls come from
  // there so the UI and the backend can never drift apart.
  useEffect(() => {
    const controller = new AbortController();
    fetchCapabilities(controller.signal)
      .then((caps) => {
        setCapabilities(caps);
        setApiError(null);
        setSettings({
          confidenceThreshold: caps.defaults.confidence_threshold,
          minCrownAreaM2: caps.defaults.min_crown_area_m2,
          engine: caps.defaults.engine,
        });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setApiError(
          error instanceof ApiError
            ? `${error.message} Start the backend with \`uvicorn app.main:app --port 8000\`.`
            : "Could not load server capabilities.",
        );
      });
    return () => controller.abort();
  }, []);

  const handleImageSelect = useCallback(
    (file: File | null) => {
      // A result describes the file it was computed from. Swapping the file
      // out from under it would leave crown polygons drawn over imagery they
      // were never found in.
      reset();
      setSelectedCrown(null);
      setImage((previous) => {
        if (previous?.objectUrl) URL.revokeObjectURL(previous.objectUrl);
        if (!file) return null;
        const objectUrl = PREVIEWABLE.test(file.name) ? URL.createObjectURL(file) : null;
        return { file, objectUrl };
      });
    },
    [reset],
  );

  // Read real pixel dimensions from the browser-decodable preview.
  useEffect(() => {
    if (!image?.objectUrl || image.width) return;
    const probe = new Image();
    probe.onload = () =>
      setImage((current) =>
        current && current.objectUrl === probe.src
          ? { ...current, width: probe.naturalWidth, height: probe.naturalHeight }
          : current,
      );
    probe.src = image.objectUrl;
  }, [image]);

  useEffect(() => {
    return () => {
      if (image?.objectUrl) URL.revokeObjectURL(image.objectUrl);
    };
  }, [image]);

  // Parse the boundary as soon as it is dropped, not at analysis time. A KML
  // for the wrong site is a mistake best discovered from the map immediately,
  // and the file has to reach the server to be measured correctly anyway.
  useEffect(() => {
    if (!boundary) {
      setBoundaryPreview(null);
      setBoundaryError(null);
      setBoundaryLoading(false);
      return;
    }
    const controller = new AbortController();
    setBoundaryLoading(true);
    setBoundaryError(null);
    previewBoundary(boundary, controller.signal)
      .then((preview) => {
        setBoundaryPreview(preview);
        setBoundaryLoading(false);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setBoundaryPreview(null);
        setBoundaryLoading(false);
        // The server's own explanation names what is wrong with the file; a
        // generic "could not read boundary" would tell the user nothing they
        // could act on.
        setBoundaryError(
          error instanceof ApiError ? error.message : "The boundary file could not be read.",
        );
      });
    return () => controller.abort();
  }, [boundary]);

  const availableEngines = useMemo(
    () => capabilities?.engines.filter((engine) => engine.available) ?? [],
    [capabilities],
  );

  const runBlockedReason = useMemo(() => {
    if (apiError) return "The backend is not reachable, so no analysis can be run.";
    if (!capabilities) return "Waiting for the backend to report its capabilities…";
    if (!image) return "Upload imagery to enable analysis.";
    if (boundary && boundaryLoading) return "Reading the boundary file…";
    if (boundary && boundaryError)
      return "The boundary file could not be read, so the analysis it would restrict cannot be set up.";
    if (availableEngines.length === 0) {
      const reasons = capabilities.engines
        .map((engine) => engine.reason)
        .filter(Boolean)
        .join(" ");
      return `No detection engine is available on this server, so no results can be produced. ${reasons}`.trim();
    }
    return null;
  }, [
    apiError,
    availableEngines.length,
    boundary,
    boundaryError,
    boundaryLoading,
    capabilities,
    image,
  ]);

  const handleRun = useCallback(() => {
    if (!image) return;
    setSelectedCrown(null);
    void run(image.file, boundary, settings);
  }, [boundary, image, run, settings]);

  const handleCancel = useCallback(() => {
    setSelectedCrown(null);
    reset();
  }, [reset]);

  return (
    <div className="flex h-full flex-col">
      <a
        href="#results"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded-md focus:bg-surface-2 focus:px-3 focus:py-2 focus:text-[13px] focus:text-ink"
      >
        Skip to results
      </a>

      <Header capabilities={capabilities} error={apiError} />

      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <Sidebar
          capabilities={capabilities}
          imageFile={image?.file ?? null}
          boundaryFile={boundary}
          boundaryPreview={boundaryPreview}
          boundaryError={boundaryError}
          boundaryLoading={boundaryLoading}
          settings={settings}
          onImageSelect={handleImageSelect}
          onBoundarySelect={setBoundary}
          onSettingsChange={setSettings}
          onRun={handleRun}
          onCancel={handleCancel}
          runBlockedReason={runBlockedReason}
          phase={analysis.phase}
          job={analysis.job}
          uploadFraction={analysis.uploadFraction}
          error={analysis.error}
        />

        <main className="flex min-h-0 min-w-0 flex-1">
          <MapView
            image={image}
            boundary={boundaryPreview}
            boundaryError={boundaryError}
            boundaryLoading={boundaryLoading}
            result={analysis.result}
            selectedId={selectedCrown}
            onSelect={setSelectedCrown}
          />
        </main>

        <ResultsPanel
          capabilities={capabilities}
          apiError={apiError}
          boundary={boundaryPreview}
          result={analysis.result}
          selectedId={selectedCrown}
          onSelect={setSelectedCrown}
        />
      </div>
    </div>
  );
}
