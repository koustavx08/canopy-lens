# CanopyLens architecture

This document describes how the system is put together, why it is split the way
it is, and which parts are deliberately not built yet.

> **Status: foundation phase complete.** The shells, contracts, configuration,
> and infrastructure described here exist and run. The detection pipeline
> (tiling, engines, georeferencing, boundary clipping, export) is scaffolded
> with real interfaces but has no engine implementations yet — see
> [Not built yet](#not-built-yet). Nothing in the UI currently fabricates a
> result to stand in for that gap; controls that cannot do real work are
> disabled with the reason shown.

## Shape of the system

```
Browser ──HTTP──> FastAPI (backend/app) ──calls──> canopylens_ml (ml/)
   │                    │                                  │
   │                    ├─ rasterio / pyproj / shapely      ├─ engine registry
   │                    │  (georeferencing, area, CRS)      └─ detection engines
   └─ MapLibre GL       └─ .data/ (uploads, results)           (pixel space only)
```

Three deployable pieces:

| Piece | Location | Responsibility |
| --- | --- | --- |
| Web client | `frontend/` | Upload, map/imagery display, crown rendering and inspection, results, export downloads |
| API | `backend/` | HTTP contract, validation, file storage, geospatial maths, orchestration of a detection run |
| Detection library | `ml/` | Turning an image array into crown polygons. Knows nothing about HTTP, CRS, or files |

## Why three pieces instead of two

`ml/` is a separate installable package (`pip install -e ./ml`) rather than a
subpackage of the API for two reasons:

1. **The heavy dependencies are isolated.** The API installs and boots with no
   torch present. A deep-learning engine is an optional extra
   (`backend/requirements-ml.txt`), not a precondition for the service running.
2. **Coordinate systems stay in one place.** Engines return polygons in *raster
   pixel coordinates*. Everything about CRS, affine transforms, reprojection,
   and area in square metres lives in the backend geo layer. There is exactly
   one place where a pixel becomes a metre, so there is exactly one place to get
   it right.

## Backend layering

```
backend/app/
  main.py             app factory: CORS, error handlers, router mounting, lifespan
  config.py           all settings, env-driven with the CANOPYLENS_ prefix
  errors.py           typed domain errors -> HTTP status + machine-readable code
  schemas.py          pydantic response models (the API contract)
  logging_config.py   logging setup
  routers/            HTTP layer: parse, validate, delegate, serialise
  services/           domain logic; no FastAPI imports
```

Routers do no domain work and services do no HTTP work. A service raises a
`CanopyLensError` subclass; the registered handler turns it into

```json
{ "error": { "code": "missing_georeference", "message": "...", "detail": {} } }
```

The `code` is the contract. The frontend branches on `code`, never on the prose
of `message`, so error text can be reworded without breaking the client.

### Error codes currently defined

| Code | HTTP | Raised when |
| --- | --- | --- |
| `unsupported_file` | 415 | Extension/format is not one the app can open |
| `invalid_raster` | 422 | The file is corrupt or unreadable as imagery |
| `missing_georeference` | 422 | Physical area was requested but the raster has no CRS/transform |
| `invalid_boundary` | 422 | KML/KMZ/GeoJSON cannot be parsed into a usable geometry |
| `engine_unavailable` | 503 | No detection engine can actually run in this process |
| `payload_too_large` | 413 | Upload exceeds `CANOPYLENS_MAX_UPLOAD_MB` |
| `not_found` | 404 | A referenced analysis or upload does not exist |
| `invalid_request` | 422 | Request-body validation failed |

## The honesty contract

The single hardest requirement of this project is that no displayed number may
be invented. That is enforced structurally, not by convention:

- **`GET /api/capabilities` reports the live process, not a static list.**
  `ml/canopylens_ml/availability.py` probes each dependency through
  `importlib.metadata` and reports the installed version or `null`. If torch is
  absent, the response says so.
- **Engines are registered as lazy factories, not instances.** Importing the
  registry never loads a model. `DetectionEngine.check()` returns
  `(available, reason)` after actually trying, and the reason string travels to
  the browser.
- **The client derives its disabled states from the server.** The Run Analysis
  button's blocked reason is computed from the capabilities response — no
  backend, no imagery, or no runnable engine each produce a specific message
  under the button. It is never a decorative control.
- **Area refuses to guess.** Area is computed only after reprojecting to a
  suitable projected CRS. Degrees of latitude and longitude are never treated as
  a linear unit. If the raster has no georeferencing, the API raises
  `missing_georeference` and the UI reports pixel-based figures labelled as
  such, instead of printing a plausible-looking m² value.

## Detection pipeline (design)

The interfaces exist; the engines are the next phase.

1. **Open the raster** with rasterio, preserving CRS, transform, and nodata.
2. **Decide tiling.** Rasters under `CANOPYLENS_MAX_PIXELS_WITHOUT_TILING`
   (default 4 MP) are processed whole. Larger ones are read window-by-window at
   `CANOPYLENS_TILE_SIZE` (800 px) with `CANOPYLENS_TILE_OVERLAP` (160 px, 20%)
   between neighbours. The full raster is never loaded into memory.
3. **Detect per tile.** The engine receives an RGB array and returns
   `CrownDetection` objects with polygons in tile-local pixel coordinates.
4. **Offset to raster space.** Tile-local coordinates are shifted by the window
   origin.
5. **Deduplicate.** A crown straddling a tile edge is found by both neighbours.
   Spatial non-maximum suppression over the overlap region keeps the
   highest-confidence polygon per cluster, using the IoU threshold in
   `DetectionParams`.
6. **Georeference.** Pixel polygons go through the raster's affine transform
   into the raster CRS.
7. **Clip to the boundary, if one was uploaded.** The KML/KMZ polygon is
   reprojected *into the raster CRS*, and detections whose centroid falls
   outside it are dropped. The analysed area becomes the boundary area, not the
   raster footprint — so canopy coverage stays honest.
8. **Measure.** Geometries are reprojected to an appropriate equal-area or local
   projected CRS before any area is computed.
9. **Serialise** to GeoJSON (WGS84) and CSV.

### Engine strategy

| Engine | Role | Dependency |
| --- | --- | --- |
| Classical CV (vegetation index + watershed segmentation) | Always-available baseline | Core requirements only |
| DeepForest (pretrained RetinaNet, boxes refined to crown polygons) | Higher-quality default when installed | Optional `requirements-ml.txt` |

`detectron2` and `detectree2` were evaluated and rejected: they need a compile
step that fails routinely on Windows and on slim container images. A reliable
engine that runs everywhere beats a better engine that does not install.

`CANOPYLENS_DEFAULT_ENGINE=auto` picks the best engine that reports itself
available. If none does, the API returns `engine_unavailable` — it does not fall
back to inventing detections.

## Frontend structure

```
frontend/src/
  App.tsx              state owner: capabilities, files, params, results
  components/
    Header.tsx         product identity
    Sidebar.tsx        upload, boundary, detection settings, run control
    MapView.tsx        MapLibre map / raster imagery preview, crown overlay
    ResultsPanel.tsx   metrics, confidence distribution, CSV + GeoJSON export
    Limitations.tsx    permanently visible, not hidden behind a disclosure
    Dropzone.tsx       shared file-input primitive
  lib/
    api.ts             typed fetch wrapper preserving the server's error code
    basemaps.ts        key-free raster basemap styles
    format.ts          number and unit formatting
  types.ts             shared response types mirroring backend/app/schemas.py
```

State lives in `App.tsx` and flows down as props. There is no state-management
library: the state is one analysis at a time, and a reducer-plus-store layer
would add indirection without removing a problem.

MapLibre is used over Leaflet for WebGL rendering — a dense field of crown
polygons stays interactive as vector geometry in a way that DOM-based overlays
do not.

Basemaps are key-free (Esri World Imagery, OpenStreetMap) so the app runs with
no account or token. `VITE_BASEMAP_STYLE_URL` overrides them.

## Development and deployment

In development, Vite proxies `/api` to `127.0.0.1:8000`, so the browser sees one
origin and CORS is never exercised. In the Docker stack, nginx does the same
thing in front of the built bundle.

Deployment targets are per-service rather than a single combined image: the
frontend is a static bundle suited to Vercel or any CDN, and the backend is a
Python service suited to Render, Railway, or Fly. **This is an intentional
deviation** from a single root Dockerfile — a combined image would push static
assets through the Python host and tie both to one release cycle, which buys
nothing here. `docker-compose.yml` still brings the whole stack up locally with
one command.

## Testing

`pytest.ini` and the root `conftest.py` put `backend/` and `ml/` on `sys.path`,
so tests import the real packages rather than a copy.

Tests assert *truthfulness*, not just status codes — for example,
`tests/backend/test_health.py` compares the version reported by
`/api/capabilities` against `importlib.metadata.version()` for the same
distribution, so a capabilities endpoint that starts lying fails the suite.

## Not built yet

Listed so the gap is explicit rather than discovered:

- Detection engine implementations (`ml/canopylens_ml/engines/` registers no
  engines yet; `_LAZY_ENGINES` is empty)
- Upload, analysis, and export endpoints
- Raster reading, tiling, and NMS deduplication
- KML/KMZ parsing and boundary clipping
- Area computation and the CRS-selection logic
- Crown polygon rendering and click-to-inspect in the map
- CSV and GeoJSON download generation
- Progress reporting for long-running analyses
