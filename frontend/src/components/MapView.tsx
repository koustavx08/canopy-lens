import { useCallback, useEffect, useRef, useState } from "react";
import {
  type GeoJSONSource,
  LngLatBounds,
  Map as MapLibreMap,
  NavigationControl,
  ScaleControl,
} from "maplibre-gl";
import type { Feature } from "geojson";
import { BASEMAPS, type BasemapKey } from "../lib/basemaps";
import { formatArea } from "../lib/format";
import type { BoundaryPreview, SelectedFile } from "../types";

interface Props {
  image: SelectedFile | null;
  boundary: BoundaryPreview | null;
  boundaryError: string | null;
  boundaryLoading: boolean;
}

type Mode = "map" | "image";

const BOUNDARY_SOURCE = "canopylens-boundary";
const BOUNDARY_FILL = "canopylens-boundary-fill";
const BOUNDARY_LINE = "canopylens-boundary-line";

/**
 * The main viewport. Two modes, because the two useful views are genuinely
 * different: a world map for georeferenced work, and a 1:1 pixel preview of the
 * uploaded file so the user can confirm they uploaded what they meant to.
 *
 * The boundary is drawn from the geometry the backend returned, already
 * reprojected to WGS84. Nothing here reprojects anything: the map shows what
 * was parsed, so what the user sees is the same geometry the analysis will
 * clip to rather than a separate browser-side approximation of it.
 */
export function MapView({ image, boundary, boundaryError, boundaryLoading }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const boundaryRef = useRef<BoundaryPreview | null>(boundary);
  const [basemap, setBasemap] = useState<BasemapKey>("satellite");
  const [mode, setMode] = useState<Mode>("map");

  boundaryRef.current = boundary;

  /**
   * Put the boundary on the map, creating the layers if they are missing.
   *
   * Changing the basemap replaces the whole style, taking every source and
   * layer with it, so this runs again on `styledata` rather than only when the
   * boundary changes.
   */
  const drawBoundary = useCallback(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const current = boundaryRef.current;

    if (!current) {
      if (map.getLayer(BOUNDARY_FILL)) map.removeLayer(BOUNDARY_FILL);
      if (map.getLayer(BOUNDARY_LINE)) map.removeLayer(BOUNDARY_LINE);
      if (map.getSource(BOUNDARY_SOURCE)) map.removeSource(BOUNDARY_SOURCE);
      return;
    }

    const data: Feature = {
      type: "Feature",
      geometry: current.geometry,
      properties: {},
    };

    const source = map.getSource(BOUNDARY_SOURCE) as GeoJSONSource | undefined;
    if (source) {
      source.setData(data);
    } else {
      map.addSource(BOUNDARY_SOURCE, { type: "geojson", data });
    }

    if (!map.getLayer(BOUNDARY_FILL)) {
      map.addLayer({
        id: BOUNDARY_FILL,
        type: "fill",
        source: BOUNDARY_SOURCE,
        // Amber, not the product green: the boundary is drawn over satellite
        // imagery of a forest, and a green outline on green canopy is an
        // outline nobody can see.
        paint: { "fill-color": "#e0b341", "fill-opacity": 0.12 },
      });
    }
    if (!map.getLayer(BOUNDARY_LINE)) {
      map.addLayer({
        id: BOUNDARY_LINE,
        type: "line",
        source: BOUNDARY_SOURCE,
        paint: { "line-color": "#e0b341", "line-width": 2 },
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
    map.on("styledata", drawBoundary);
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

  // A new boundary is the one thing worth interrupting the current view for:
  // the user needs to see where it landed to know they uploaded the right file.
  useEffect(() => {
    drawBoundary();
    const map = mapRef.current;
    if (!map || !boundary) return;
    setMode("map");
    const [minLon, minLat, maxLon, maxLat] = boundary.bounds_wgs84;
    map.fitBounds(new LngLatBounds([minLon, minLat], [maxLon, maxLat]), {
      padding: 64,
      maxZoom: 18,
      duration: 700,
    });
  }, [boundary, drawBoundary]);

  // Switch to the pixel preview automatically when a new image is selected.
  useEffect(() => {
    if (image) setMode("image");
  }, [image]);

  // MapLibre needs a nudge when its container is revealed or resized.
  useEffect(() => {
    if (mode === "map") {
      mapRef.current?.resize();
      drawBoundary();
    }
  }, [mode, drawBoundary]);

  return (
    <div className="relative min-h-[320px] flex-1 bg-surface-0">
      <div
        ref={containerRef}
        className={`absolute inset-0 ${mode === "map" ? "" : "invisible"}`}
        aria-hidden={mode !== "map"}
      />

      {mode === "image" && (
        <div className="absolute inset-0 flex items-center justify-center p-6">
          {image?.objectUrl ? (
            <figure className="flex max-h-full max-w-full flex-col items-center gap-3">
              <img
                src={image.objectUrl}
                alt={`Preview of ${image.file.name}`}
                className="max-h-[calc(100vh-260px)] max-w-full border border-line object-contain"
              />
              <figcaption className="tabular font-mono text-[11px] text-ink-faint">
                {image.file.name}
                {image.width ? ` · ${image.width} × ${image.height} px` : ""}
              </figcaption>
            </figure>
          ) : (
            <p className="max-w-sm text-center text-[13px] leading-relaxed text-ink-faint">
              {image
                ? "This file cannot be previewed in the browser. GeoTIFFs are rendered server-side once analysis runs."
                : "No imagery loaded yet."}
            </p>
          )}
        </div>
      )}

      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between p-3">
        <div className="pointer-events-auto flex overflow-hidden rounded-md border border-line bg-surface-1/95 backdrop-blur">
          {(["map", "image"] as Mode[]).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setMode(value)}
              disabled={value === "image" && !image}
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

      {mode === "map" && (boundary || boundaryError || boundaryLoading) && (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center p-3">
          <div className="pointer-events-auto max-w-[min(32rem,calc(100%-1rem))] rounded-md border border-line bg-surface-1/95 px-3 py-2 backdrop-blur">
            {boundaryLoading && (
              <p className="text-[12px] text-ink-muted">Reading boundary…</p>
            )}
            {boundaryError && !boundaryLoading && (
              <p className="text-[12px] leading-relaxed text-danger">{boundaryError}</p>
            )}
            {boundary && !boundaryLoading && !boundaryError && (
              <>
                <p className="tabular text-[12px] text-ink">
                  <span className="text-ink-muted">Boundary</span>{" "}
                  {formatArea(boundary.area_m2)}
                  {boundary.part_count > 1 ? ` · ${boundary.part_count} parts` : ""}
                  {boundary.has_holes ? " · with holes" : ""}
                </p>
                <p className="mt-0.5 text-[11px] text-ink-faint">
                  Enclosed by the boundary file, measured in {boundary.measurement_crs}. This is
                  not the analysed area.
                </p>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
