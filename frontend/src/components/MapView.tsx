import { useEffect, useRef, useState } from "react";
import {
  Map as MapLibreMap,
  NavigationControl,
  ScaleControl,
} from "maplibre-gl";
import { BASEMAPS, type BasemapKey } from "../lib/basemaps";
import type { SelectedFile } from "../types";

interface Props {
  image: SelectedFile | null;
}

type Mode = "map" | "image";

/**
 * The main viewport. Two modes, because the two useful views are genuinely
 * different: a world map for georeferenced work, and a 1:1 pixel preview of the
 * uploaded file so the user can confirm they uploaded what they meant to.
 */
export function MapView({ image }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const [basemap, setBasemap] = useState<BasemapKey>("satellite");
  const [mode, setMode] = useState<Mode>("map");

  // Switch to the pixel preview automatically when a new image is selected.
  useEffect(() => {
    if (image) setMode("image");
  }, [image]);

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

  // MapLibre needs a nudge when its container is revealed or resized.
  useEffect(() => {
    if (mode === "map") mapRef.current?.resize();
  }, [mode]);

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
    </div>
  );
}
