import type { StyleSpecification } from "maplibre-gl";

/**
 * Raster basemaps that need no API key, so the app works for a stranger who
 * just cloned the repo. Override with VITE_BASEMAP_STYLE_URL to point at a
 * vector style (e.g. a self-hosted or commercial tile service).
 */
function rasterStyle(tiles: string[], attribution: string, maxzoom = 19): StyleSpecification {
  return {
    version: 8,
    // Text needs glyphs, and a raster style carries none. Crown ID labels are
    // the only text drawn on the map; if this endpoint is unreachable MapLibre
    // simply omits them and every other layer still renders.
    glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
    sources: {
      base: { type: "raster", tiles, tileSize: 256, attribution, maxzoom },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#0a0c0b" } },
      { id: "base", type: "raster", source: "base" },
    ],
  };
}

export const BASEMAPS = {
  satellite: {
    label: "Satellite",
    style: rasterStyle(
      [
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      ],
      "Imagery © Esri, Maxar, Earthstar Geographics",
    ),
  },
  streets: {
    label: "Streets",
    style: rasterStyle(
      ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      "© OpenStreetMap contributors",
    ),
  },
} as const;

export type BasemapKey = keyof typeof BASEMAPS;

/** Font stack for map labels; MapLibre falls through the list in order. */
export const LABEL_FONT = ["Open Sans Regular", "Noto Sans Regular"];
