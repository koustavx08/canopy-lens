# CanopyLens architecture

This document describes how the system is put together, why it is split the way
it is, and which parts are deliberately not built yet.

> **Status: detection pipeline complete.** Engines, tiling, deduplication,
> CRS handling, boundary clipping, area measurement and export are implemented
> and covered by 244 tests. What remains is the HTTP layer that exposes them
> and the frontend wiring that displays them — see
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

## Detection pipeline

Implemented across `backend/app/services/` and `ml/canopylens_ml/`. The entry
point is `analysis.analyze_image()`.

| Step | Module | What it does |
| --- | --- | --- |
| 1. Open | `services/raster.py` | rasterio open, preserving CRS, transform and nodata. Fewer than three bands is refused |
| 2. Measurement CRS | `services/geo.py` | UTM zone for a normal scene, local equal-area for a wide extent, source CRS kept if already metric |
| 3. Threshold conversion | `services/geo.py` | The m2 minimum becomes pixels by projecting one real pixel |
| 4. Tile | `services/raster.py` | 800 px windows, 160 px overlap, above 4 MP. Edge tiles shift inward rather than truncate |
| 5. Detect | `canopylens_ml/engines/` | Engine returns polygons in tile-local pixel coordinates |
| 6. Offset | `canopylens_ml/geometry.py` | translate() lifts tile coordinates into the full raster grid |
| 7. Deduplicate | `services/dedup.py` | IoU non-maximum suppression plus a containment rule, over an R-tree |
| 8. Filter | `services/analysis.py` | Confidence, then minimum area |
| 9. Georeference and measure | `services/geo.py` | Affine to raster CRS, reproject to metric CRS, repair, measure |
| 10. Clip | `services/boundary.py` | Boundary reprojected into the raster CRS; crowns outside it are excluded |
| 11. Summarise | `services/analysis.py` | Counts, areas, coverage, statistics, confidence distribution |
| 12. Export | `services/export.py` | CSV and GeoJSON |

### Why deduplication needs more than IoU

A crown straddling a tile seam is found by both neighbours, so overlap
necessarily produces duplicates: without suppression a large raster overcounts
along every seam.

IoU alone is not enough. When one tile merges two neighbouring crowns into a
single blob and the other resolves one of them cleanly, the small polygon sits
almost entirely inside the large one but scores an IoU around 0.2 — below any
sensible threshold — while plainly being the same tree twice. The containment
rule measures the intersection against the *smaller* polygon's area and catches
exactly that case.

### Why area is the union, not the sum

Overlapping crowns cover one patch of ground. Summing their areas double-counts
the overlap and lets coverage exceed 100%. `total_canopy_area_m2` is therefore
the area of the union of crown polygons, clipped to the analysed region.
`sum_crown_area_m2` is reported alongside it because that is the figure a user
gets by adding up the CSV's area column, and the two differing is expected
rather than a bug.

### Engine strategy

| Engine | Role | Confidence | Dependency |
| --- | --- | --- | --- |
| `classical` — Excess Green index + distance-transform watershed | Always-available baseline | Heuristic: greenness, circularity, solidity | Core requirements only |
| `deepforest` — pretrained RetinaNet, boxes refined to crown polygons | Higher-quality default when installed | Model score from the trained detector | Optional `requirements-ml.txt` |

`EngineInfo.confidence_kind` carries "heuristic" or "model" all the way to the
browser. The distinction is not cosmetic: presenting a rule-based ranking as
though it were a detector's probability would be exactly the kind of
plausible-looking falsehood this project is built to avoid.

`detectron2` and `detectree2` were evaluated and rejected: they need a compile
step that fails routinely on Windows and on slim container images. A reliable
engine that runs everywhere beats a better engine that does not install.

`CANOPYLENS_DEFAULT_ENGINE=auto` picks the best engine that reports itself
available. If none does, the API returns `engine_unavailable` — it does not
fall back to inventing detections.

### Availability must be probed, not declared

`DeepForestEngine.check()` imports `deepforest.main`, not the bare `deepforest`
package. During development the shallow check passed while the engine then
crashed on use, because DeepForest 1.5 imports `albumentations.functional` and
albumentations 2.x had removed it. An availability check that does not exercise
the real entry point is a claim, not a check; the version ceiling is now pinned
and the probe goes deep enough to catch the next one.

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

The geospatial tests work on synthetic scenes whose truth is arithmetic: crowns
are circles of known radius at known pixel positions on rasters of known pixel
size, so a count or an area is checked against what the geometry says it must
be rather than against previously recorded output. A test that only pins
current behaviour cannot tell you the behaviour is correct.

`tests/geo/test_area.py` attacks the degrees trap directly. The same ground
expressed in UTM and in WGS84 must measure the same; a one-degree box at 60
degrees north must measure far less ground than one at the equator despite
being identical in degrees; and the naive square-degrees answer is asserted to
be wrong by nine orders of magnitude, so a regression reads as an obvious
failure rather than as a plausible number.

| Area | File |
| --- | --- |
| CRS selection, UTM zones, reprojection | `tests/geo/test_crs.py` |
| Area measurement, the degrees trap, geometry repair | `tests/geo/test_area.py` |
| KML/KMZ/GeoJSON parsing and clipping | `tests/geo/test_boundary.py` |
| Duplicate removal | `tests/geo/test_dedup.py` |
| Raster metadata, tiling windows, dtype handling | `tests/geo/test_raster.py` |
| End-to-end analysis, filtering, summary statistics | `tests/pipeline/test_analysis.py` |
| CSV and GeoJSON export | `tests/pipeline/test_export.py` |
| Engines, registry, pixel-space primitives | `tests/ml/` |

## Not built yet

Listed so the gap is explicit rather than discovered:

- HTTP endpoints for upload, analysis and export. The pipeline is callable as
  `analyze_image()` / `export_csv()` / `export_geojson()`, but nothing serves it
  over the network yet
- Progress reporting for long-running analyses
- Crown polygon rendering and click-to-inspect in the map
- Frontend wiring to real results; the UI still shows em dashes and a disabled
  Run control with its reason
- Deployment configuration for a hosted environment
