import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type GeoJSONSource,
  LngLatBounds,
  Map as MapLibreMap,
  NavigationControl,
  ScaleControl,
} from "maplibre-gl";
import type { Feature, FeatureCollection, Geometry, Position } from "geojson";
import { BASEMAPS, LABEL_FONT, type BasemapKey } from "../lib/basemaps";
import { formatArea } from "../lib/format";
import type { AnalysisResult, BoundaryPreview, Detection, SelectedFile } from "../types";

interface Props {
  image: SelectedFile | null;
  boundary: BoundaryPreview | null;
  boundaryError: string | null;
  boundaryLoading: boolean;
  result: AnalysisResult | null;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

type Mode = "map" | "image";

const BOUNDARY_SOURCE = "canopylens-boundary";
const BOUNDARY_FILL = "canopylens-boundary-fill";
const BOUNDARY_LINE = "canopylens-boundary-line";
const CROWN_SOURCE = "canopylens-crowns";
const CROWN_FILL = "canopylens-crown-fill";
const CROWN_LINE = "canopylens-crown-line";
const CROWN_SELECTED = "canopylens-crown-selected";
const CROWN_LABEL = "canopylens-crown-label";

const EMPTY: FeatureCollection = { type: "FeatureCollection", features: [] };

/**
 * The main viewport. Two modes, because the two useful views are genuinely
 * different: a world map for georeferenced work, and a 1:1 pixel view of the
 * uploaded file, which is the only way to see crowns found in imagery that
 * carries no coordinate system.
 *
 * Nothing here reprojects anything. The boundary and the crown polygons are
 * drawn from the WGS84 geometry the backend returned, so what the user sees is
 * the same geometry the analysis measured rather than a browser-side
 * approximation of it.
 */
export function MapView({
  image,
  boundary,
  boundaryError,
  boundaryLoading,
  result,
  selectedId,
  onSelect,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [basemap, setBasemap] = useState<BasemapKey>("satellite");
  const [mode, setMode] = useState<Mode>("map");

  const crowns = useMemo(() => crownCollection(result), [result]);
  const georeferenced = crowns.features.length > 0;

  // Read inside map callbacks that are registered once and must not close over
  // a stale render.
  const boundaryRef = useRef(boundary);
  const crownsRef = useRef(crowns);
  const selectedRef = useRef(selectedId);
  const selectRef = useRef(onSelect);
  // What each source currently holds, so a repeated draw does not re-parse
  // thousands of polygons that have not changed.
  const drawnRef = useRef<Record<string, unknown>>({});
  boundaryRef.current = boundary;
  crownsRef.current = crowns;
  selectedRef.current = selectedId;
  selectRef.current = onSelect;

  /**
   * Put the overlays on the map, creating the layers if they are missing.
   *
   * Changing the basemap replaces the whole style, taking every source and
   * layer with it, so this runs again on `styledata` rather than only when the
   * data changes.
   */
  const draw = useCallback(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;

    syncSource(
      map,
      drawnRef.current,
      BOUNDARY_SOURCE,
      boundaryRef.current ? boundaryFeature(boundaryRef.current) : null,
    );
    syncSource(
      map,
      drawnRef.current,
      CROWN_SOURCE,
      crownsRef.current.features.length ? crownsRef.current : null,
    );

    if (map.getSource(BOUNDARY_SOURCE) && !map.getLayer(BOUNDARY_FILL)) {
      map.addLayer({
        id: BOUNDARY_FILL,
        type: "fill",
        source: BOUNDARY_SOURCE,
        // Amber, not the product green: the boundary is drawn over satellite
        // imagery of a forest, and a green outline on green canopy is an
        // outline nobody can see.
        paint: { "fill-color": "#e0b341", "fill-opacity": 0.08 },
      });
      map.addLayer({
        id: BOUNDARY_LINE,
        type: "line",
        source: BOUNDARY_SOURCE,
        paint: { "line-color": "#e0b341", "line-width": 2 },
      });
    }

    if (map.getSource(CROWN_SOURCE) && !map.getLayer(CROWN_FILL)) {
      map.addLayer({
        id: CROWN_FILL,
        type: "fill",
        source: CROWN_SOURCE,
        paint: { "fill-color": "#3ddc84", "fill-opacity": 0.22 },
      });
      map.addLayer({
        id: CROWN_LINE,
        type: "line",
        source: CROWN_SOURCE,
        paint: { "line-color": "#3ddc84", "line-width": 1.2, "line-opacity": 0.9 },
      });
      map.addLayer({
        id: CROWN_SELECTED,
        type: "line",
        source: CROWN_SOURCE,
        filter: ["==", ["get", "id"], selectedRef.current ?? ""],
        paint: { "line-color": "#ffffff", "line-width": 3 },
      });
      map.addLayer({
        id: CROWN_LABEL,
        type: "symbol",
        source: CROWN_SOURCE,
        // Only once the crowns are large enough on screen to tell apart: a
        // thousand overlapping numbers is not a label, it is noise.
        minzoom: 17,
        layout: {
          "text-field": ["get", "label"],
          "text-font": LABEL_FONT,
          "text-size": 11,
          "text-allow-overlap": false,
        },
        paint: {
          "text-color": "#e9edeb",
          "text-halo-color": "#0a0c0b",
          "text-halo-width": 1.2,
        },
      });
    }
  }, []);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const styleOverride = import.meta.env.VITE_BASEMAP_STYLE_URL;
    const map = new MapLibreMap({
      container: containerRef.current,
      style: styleOverride || BASEMAPS[basemap].style,
      center: [0, 20],
      zoom: 1.6,
      attributionControl: { compact: true },
    });
    map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new ScaleControl({ unit: "metric" }), "bottom-left");
    map.on("styledata", draw);
    // `styledata` can fire while the style is still loading, in which case the
    // draw above bails out. `idle` fires once the map has settled and is the
    // backstop that stops a slow style change from leaving a blank overlay.
    map.on("idle", draw);

    // Registered on the map rather than on a layer: the layers are destroyed
    // and rebuilt every time the basemap style changes.
    map.on("click", (event) => {
      if (!map.getLayer(CROWN_FILL)) return;
      const hit = map.queryRenderedFeatures(event.point, { layers: [CROWN_FILL] })[0];
      selectRef.current(hit ? (String(hit.properties?.id ?? "") || null) : null);
    });
    map.on("mousemove", (event) => {
      if (!map.getLayer(CROWN_FILL)) return;
      const over = map.queryRenderedFeatures(event.point, { layers: [CROWN_FILL] }).length > 0;
      map.getCanvas().style.cursor = over ? "pointer" : "";
    });

    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
    // Basemap changes are handled by the effect below; this runs once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!mapRef.current || import.meta.env.VITE_BASEMAP_STYLE_URL) return;
    mapRef.current.setStyle(BASEMAPS[basemap].style);
  }, [basemap]);

  // A new boundary is worth interrupting the current view for: the user needs
  // to see where it landed to know they uploaded the right file.
  useEffect(() => {
    draw();
    const map = mapRef.current;
    if (!map || !boundary) return;
    setMode("map");
    const [minLon, minLat, maxLon, maxLat] = boundary.bounds_wgs84;
    map.fitBounds(new LngLatBounds([minLon, minLat], [maxLon, maxLat]), {
      padding: 64,
      maxZoom: 18,
      duration: 700,
    });
  }, [boundary, draw]);

  // Results decide which view can actually show them. Crowns from imagery with
  // no coordinate system exist only in pixel space, so the map has nothing to
  // draw and the pixel view is the honest place to look.
  useEffect(() => {
    if (!result) return;
    draw();
    setMode(georeferenced ? "map" : "image");
    const map = mapRef.current;
    if (!map || !georeferenced) return;
    const bounds = collectionBounds(crowns);
    if (bounds) map.fitBounds(bounds, { padding: 64, maxZoom: 19, duration: 700 });
  }, [result, crowns, georeferenced, draw]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer(CROWN_SELECTED)) return;
    map.setFilter(CROWN_SELECTED, ["==", ["get", "id"], selectedId ?? ""]);
  }, [selectedId, result]);

  // Bring the selected crown into view however it was selected: by clicking the
  // map, or by stepping through the crowns from the keyboard.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !selectedId || mode !== "map") return;
    const feature = crowns.features.find((item) => item.properties?.id === selectedId);
    const bounds = feature ? geometryBounds(feature.geometry) : null;
    if (bounds && !map.getBounds().contains(bounds.getCenter())) {
      map.easeTo({ center: bounds.getCenter(), duration: 500 });
    }
  }, [selectedId, crowns, mode]);

  // Switch to the pixel view when a new image is selected, so the user can
  // confirm they uploaded what they meant to.
  useEffect(() => {
    if (image) setMode("image");
  }, [image]);

  // MapLibre needs a nudge when its container is revealed or resized.
  useEffect(() => {
    if (mode === "map") {
      mapRef.current?.resize();
      draw();
    }
  }, [mode, draw]);

  return (
    <div className="relative min-h-[320px] flex-1 bg-surface-0">
      <div
        ref={containerRef}
        className={`absolute inset-0 ${mode === "map" ? "" : "invisible"}`}
        aria-hidden={mode !== "map"}
      />

      {mode === "image" && (
        <PixelView image={image} result={result} selectedId={selectedId} onSelect={onSelect} />
      )}

      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between p-3">
        <div className="pointer-events-auto flex overflow-hidden rounded-md border border-line bg-surface-1/95 backdrop-blur">
          {(["map", "image"] as Mode[]).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setMode(value)}
              disabled={value === "image" && !image}
              aria-pressed={mode === value}
              className={`px-3 py-1.5 text-[12px] transition-colors disabled:cursor-not-allowed disabled:text-ink-faint ${
                mode === value ? "bg-surface-2 text-ink" : "text-ink-muted hover:text-ink"
              }`}
            >
              {value === "map" ? "Map" : "Imagery"}
            </button>
          ))}
        </div>

        {mode === "map" && !import.meta.env.VITE_BASEMAP_STYLE_URL && (
          <div className="pointer-events-auto flex overflow-hidden rounded-md border border-line bg-surface-1/95 backdrop-blur">
            {(Object.keys(BASEMAPS) as BasemapKey[]).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setBasemap(key)}
                aria-pressed={basemap === key}
                className={`px-3 py-1.5 text-[12px] transition-colors ${
                  basemap === key ? "bg-surface-2 text-ink" : "text-ink-muted hover:text-ink"
                }`}
              >
                {BASEMAPS[key].label}
              </button>
            ))}
          </div>
        )}
      </div>

      <Caption
        mode={mode}
        boundary={boundary}
        boundaryError={boundaryError}
        boundaryLoading={boundaryLoading}
        result={result}
        crownCount={crowns.features.length}
      />
    </div>
  );
}

/**
 * The pixel view: the uploaded image with crown outlines drawn over it.
 *
 * The overlay is an SVG whose viewBox is the image's own pixel grid, so the
 * polygons the backend measured in pixels need no scaling here — the browser
 * does it, and it stays correct at any window size. This is also the only view
 * that can show crowns found in imagery with no coordinate system.
 */
function PixelView({
  image,
  result,
  selectedId,
  onSelect,
}: {
  image: SelectedFile | null;
  result: AnalysisResult | null;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const width = result?.image_metadata.width_px ?? image?.width;
  const height = result?.image_metadata.height_px ?? image?.height;
  const detections = result?.detections ?? [];

  if (!image?.objectUrl || !width || !height) {
    return (
      <div className="absolute inset-0 flex items-center justify-center p-6">
        <p className="max-w-sm text-center text-[13px] leading-relaxed text-ink-faint">
          {!image
            ? "No imagery loaded yet."
            : "This file cannot be displayed in the browser — a GeoTIFF is not a web image format. The map view shows the crowns found in it at their real-world position."}
        </p>
      </div>
    );
  }

  return (
    <div className="absolute inset-0 flex items-center justify-center p-6">
      <figure className="flex max-h-full min-h-0 max-w-full flex-col items-center gap-3">
        <div
          className="relative max-h-[calc(100vh-280px)] max-w-full border border-line"
          style={{ aspectRatio: `${width} / ${height}` }}
        >
          <img
            src={image.objectUrl}
            alt={`Uploaded imagery: ${image.file.name}`}
            className="block h-full w-full object-contain"
          />
          {detections.length > 0 && (
            <svg
              viewBox={`0 0 ${width} ${height}`}
              className="absolute inset-0 h-full w-full"
              role="group"
              aria-label={`${detections.length} detected crowns`}
            >
              {detections.map((crown) => (
                <CrownShape
                  key={crown.id}
                  crown={crown}
                  selected={crown.id === selectedId}
                  onSelect={onSelect}
                />
              ))}
            </svg>
          )}
        </div>
        <figcaption className="tabular font-mono text-[11px] text-ink-faint">
          {result?.image_metadata.filename ?? image.file.name} · {width} × {height} px
          {detections.length > 0 ? ` · ${detections.length} crowns` : ""}
        </figcaption>
      </figure>
    </div>
  );
}

/** One crown in pixel space: clickable, focusable, and legible at any zoom. */
function CrownShape({
  crown,
  selected,
  onSelect,
}: {
  crown: Detection;
  selected: boolean;
  onSelect: (id: string | null) => void;
}) {
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={`Crown ${crown.id}, confidence ${crown.confidence.toFixed(2)}`}
      aria-pressed={selected}
      className="cursor-pointer"
      onClick={() => onSelect(selected ? null : crown.id)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect(selected ? null : crown.id);
        }
      }}
    >
      {rings(crown.geometry_px).map((ring, index) => (
        <polygon
          key={index}
          points={ring.map(([x, y]) => `${x},${y}`).join(" ")}
          fill="#3ddc84"
          fillOpacity={selected ? 0.35 : 0.18}
          stroke={selected ? "#ffffff" : "#3ddc84"}
          // Stroke width in screen pixels, not image pixels, so outlines stay
          // hairline-thin on a 20 000 px mosaic and visible on a small crop.
          strokeWidth={selected ? 2.5 : 1.2}
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </g>
  );
}

/** The readout under the viewport: what is drawn, and what it means. */
function Caption({
  mode,
  boundary,
  boundaryError,
  boundaryLoading,
  result,
  crownCount,
}: {
  mode: Mode;
  boundary: BoundaryPreview | null;
  boundaryError: string | null;
  boundaryLoading: boolean;
  result: AnalysisResult | null;
  crownCount: number;
}) {
  const showBoundary = mode === "map" && (boundary || boundaryError || boundaryLoading);
  const showCrowns = mode === "map" && result !== null;
  if (!showBoundary && !showCrowns) return null;

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center p-3">
      <div className="pointer-events-auto max-w-[min(36rem,calc(100%-1rem))] rounded-md border border-line bg-surface-1/95 px-3 py-2 backdrop-blur">
        {boundaryLoading && <p className="text-[12px] text-ink-muted">Reading boundary…</p>}
        {boundaryError && !boundaryLoading && (
          <p className="text-[12px] leading-relaxed text-danger">{boundaryError}</p>
        )}
        {boundary && !boundaryLoading && !boundaryError && (
          <p className="tabular text-[12px] text-ink">
            <span className="text-ink-muted">Boundary</span> {formatArea(boundary.area_m2)}
            {boundary.part_count > 1 ? ` · ${boundary.part_count} parts` : ""}
            {boundary.has_holes ? " · with holes" : ""}
            <span className="text-ink-faint"> — what the file encloses, not the analysed area</span>
          </p>
        )}
        {showCrowns && result && (
          <p className="tabular mt-0.5 text-[12px] text-ink">
            <span className="text-ink-muted">Crowns</span> {crownCount} drawn
            {crownCount === 0 && result.summary.tree_count > 0
              ? " — this imagery carries no coordinate system, so its crowns can only be shown in the Imagery view"
              : " · click one to inspect it"}
          </p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// GeoJSON plumbing
// ---------------------------------------------------------------------------

function boundaryFeature(boundary: BoundaryPreview): Feature {
  return { type: "Feature", geometry: boundary.geometry, properties: {} };
}

/** Crowns that have a real-world position. The rest exist only in pixel space. */
function crownCollection(result: AnalysisResult | null): FeatureCollection {
  if (!result) return EMPTY;
  const features = result.detections
    .filter((crown): crown is Detection & { geometry: Geometry } => crown.geometry !== null)
    .map<Feature>((crown) => ({
      type: "Feature",
      geometry: crown.geometry,
      properties: {
        id: crown.id,
        // The full id is "crown_00042"; on the map the number alone is legible,
        // and the inspector shows the whole thing.
        label: crown.id.replace(/^crown_0*/, ""),
        confidence: crown.confidence,
      },
    }));
  return { type: "FeatureCollection", features };
}

function syncSource(
  map: MapLibreMap,
  drawn: Record<string, unknown>,
  id: string,
  data: Feature | FeatureCollection | null,
) {
  const existing = map.getSource(id) as GeoJSONSource | undefined;
  const wanted = data ?? EMPTY;
  if (existing) {
    // A style reload destroys the source, so the record of what it held is
    // stale; `existing` being absent is what tells us to write again.
    if (drawn[id] !== wanted) existing.setData(wanted);
  } else {
    map.addSource(id, { type: "geojson", data: wanted });
  }
  drawn[id] = wanted;
}

function rings(geometry: Geometry): Position[][] {
  if (geometry.type === "Polygon") return geometry.coordinates;
  if (geometry.type === "MultiPolygon") return geometry.coordinates.flat();
  return [];
}

function geometryBounds(geometry: Geometry): LngLatBounds | null {
  const positions = rings(geometry).flat();
  if (positions.length === 0) return null;
  const bounds = new LngLatBounds();
  for (const [lon, lat] of positions) bounds.extend([lon, lat]);
  return bounds;
}

function collectionBounds(collection: FeatureCollection): LngLatBounds | null {
  const bounds = new LngLatBounds();
  let any = false;
  for (const feature of collection.features) {
    for (const [lon, lat] of rings(feature.geometry).flat()) {
      bounds.extend([lon, lat]);
      any = true;
    }
  }
  return any ? bounds : null;
}
