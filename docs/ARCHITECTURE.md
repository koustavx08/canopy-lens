# CanopyLens architecture

This document describes how the system is put together, why it is split the way
it is, and which parts are deliberately not built yet.

> **Status: detection pipeline and boundary workflow complete.** Engines,
> tiling, deduplication, CRS handling, boundary parsing and measurement,
> clipping, area measurement and export are implemented and covered by 341
> tests. Boundaries run end to end: `POST /api/boundary/preview` parses and
> measures one, and the map draws it. What remains is the analysis endpoint and
> the crown rendering that depends on it — see
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
    health.py         liveness and capability reporting
    boundary.py       boundary upload, parsing and measurement
  services/           domain logic; no FastAPI imports
    raster.py         rasterio reading, tiling, dtype normalisation
    geo.py            CRS selection, reprojection, area in m2 — the only place
                      a pixel becomes a metre
    boundary.py       KML/KMZ/GeoJSON parsing, repair and measurement
    dedup.py          non-maximum suppression across tile seams
    analysis.py       the orchestrator: analyze_image()
    export.py         CSV and GeoJSON
    uploads.py        streamed, size-limited receipt of uploaded files
    capabilities.py   what this process can actually do, read from the process
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
| `payload_too_large` | 413 | Upload exceeds `CANOPYLENS_MAX_UPLOAD_MB`, or `CANOPYLENS_MAX_BOUNDARY_UPLOAD_MB` for a boundary file |
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
| 10. Clip and measure the boundary | `services/boundary.py` | Boundary parsed, repaired, measured in the imagery's metric CRS, then reprojected into the raster CRS; crowns whose centroid falls outside are excluded. See [The boundary workflow](#the-boundary-workflow) |
| 11. Summarise | `services/analysis.py` | Counts, the three areas, coverage, statistics, confidence distribution |
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

## The boundary workflow

`services/boundary.py` reads the file; `services/geo.py` measures it;
`routers/boundary.py` exposes it; `services/analysis.py` applies it. The split
matters: the parser knows about XML and archives, the geo layer knows about
projections, and neither knows about HTTP.

### Why KML is parsed directly rather than through GDAL

GDAL's KML driver is optional. Whether it is present depends on how the wheel
or the container image was built, so a boundary feature that relies on it works
on the developer's machine and fails in production for reasons that have
nothing to do with the file. KML is a small, well specified XML format and the
subset that matters — polygons, inner boundaries, multi-geometries — is short
to read directly. Tag matching ignores namespaces, because real exports declare
several different ones and some declare none.

### Why repair happens before the union, not after

A hand-drawn boundary that crosses itself is a bow-tie, and `unary_union`
raises on some of them. Repairing each polygon individually first means one bad
ring in a twenty-stand file costs that ring rather than the whole upload. The
same rule applies to the result: `all_polygons` keeps every part of a boundary,
unlike `largest_polygon` which is used for crowns — dropping the smaller parts
of a multi-stand boundary would silently shrink the region being analysed.

### Why a declared CRS is honoured

KML is WGS84 by the OGC specification and GeoJSON is WGS84 by RFC 7946, so
neither is inferred from the magnitude of the coordinates. The exception is the
pre-RFC 7946 `crs` member, which QGIS and ArcGIS still emit. Ignoring it means
reading an easting of 500000 as a longitude and placing an Ontario woodlot in
the Gulf of Guinea. It is honoured, and a warning says that it was.

### Why the boundary moves to the imagery

The boundary is reprojected into the raster's CRS, not the other way round.
Reprojecting the raster would resample it, altering pixel values before any
detection runs — the detection would then be of a slightly different image than
the one the user uploaded.

### Three areas that must not be confused

| Figure | Definition | Reported on |
| --- | --- | --- |
| Boundary area | Ground the file encloses, holes excluded | `AnalysisResult.boundary.area_m2` |
| Analysed area | Imagery footprint ∩ boundary, minus nodata | `AnalysisSummary.analyzed_area_m2` |
| Detected crown area | Union of crown polygons clipped to the analysed region | `AnalysisSummary.total_canopy_area_m2` |

```text
canopy coverage % = detected crown area / analysed area * 100
```

The denominator is the analysed area. Dividing by the boundary area instead
would halve the coverage for a boundary twice the size of the image, and the
result would look entirely plausible — which is precisely why the three live in
separate fields with separate names rather than as one reused `area`.

When the imagery does not cover the whole boundary, `imagery_covers_pct`
records the shortfall and a warning states it in square metres. A boundary
larger than the image is a normal thing to upload; a result that quietly
describes the overlap while appearing to describe the stand is not.

### Membership is decided by the centroid

A crown belongs to the boundary when its centroid does. Requiring the whole
polygon to be inside would drop every tree on the forest edge; accepting any
overlap would pull in trees that are mostly outside. `covers` rather than
`contains`, so a centroid landing exactly on the line counts as inside. The
crown's *area* contribution is still clipped to the analysed region, so a tree
on the edge is counted once and measured only where it is.

### The preview endpoint exists to prevent a slow mistake

`POST /api/boundary/preview` parses and measures a boundary and returns it as
WGS84 geometry. A user who uploaded the wrong KML finds out from the map in a
second rather than from a tree count twenty minutes later that quietly
describes the wrong stand. The upload is read and discarded inside the request:
a preview is answered immediately, so storing it would only accumulate files
nobody will ask for again.

The area is computed on the server, not in the browser. Measuring it correctly
requires reprojection into a chosen projected CRS, and that is done in exactly
one place; a number produced twice by two code paths is a number that will
eventually disagree with itself.

## Frontend structure

```
frontend/src/
  App.tsx              state owner: capabilities, files, params, results
  components/
    Header.tsx         product identity
    Sidebar.tsx        upload, boundary, detection settings, run control
    MapView.tsx        MapLibre map / raster imagery preview, boundary layer,
                       crown overlay
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

The boundary is drawn from the geometry the server returned, so the outline on
screen is the same geometry the analysis will clip to rather than a separate
browser-side parse of the same file. Changing the basemap replaces the whole
MapLibre style, taking every source and layer with it, so the boundary layer is
re-added on `styledata` rather than only when the boundary changes. It is drawn
in amber: the layer sits over satellite imagery of a forest, and a green
outline on green canopy is an outline nobody can see.

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

The boundary tests follow the same rule. Stands are rectangles written in
projected metres — `utm_rect(500000, 4649000, 500100, 4649100)` is a hundred
metre square whether or not the code agrees — and converted to degrees only at
the point the file is written. The expected area is then the rectangle's own
arithmetic, so `10000` is a fact about the geometry rather than a value
recorded from an earlier run.

| Area | File |
| --- | --- |
| CRS selection, UTM zones, reprojection | `tests/geo/test_crs.py` |
| Area measurement, the degrees trap, geometry repair | `tests/geo/test_area.py` |
| KML/KMZ/GeoJSON parsing, repair, holes, multi-part, declared CRS | `tests/geo/test_boundary.py` |
| Boundary area in m2, holes, the degrees trap, projection choice | `tests/geo/test_boundary_area.py` |
| Duplicate removal | `tests/geo/test_dedup.py` |
| Raster metadata, tiling windows, dtype handling | `tests/geo/test_raster.py` |
| End-to-end analysis, filtering, summary statistics, boundary clipping | `tests/pipeline/test_analysis.py` |
| CSV and GeoJSON export | `tests/pipeline/test_export.py` |
| Health, capabilities, the boundary endpoint | `tests/backend/` |
| Upload size limits and filename handling | `tests/backend/test_uploads.py` |
| Engines, registry, pixel-space primitives | `tests/ml/` |

## Not built yet

Listed so the gap is explicit rather than discovered:

- HTTP endpoints for imagery upload, analysis and export. The pipeline is
  callable as `analyze_image()` / `export_csv()` / `export_geojson()`, but
  nothing serves those over the network yet. `POST /api/boundary/preview` is
  the one analysis-adjacent endpoint that does exist
- Progress reporting for long-running analyses
- Crown polygon rendering and click-to-inspect in the map
- Frontend wiring to real detection results; the detection metrics still show
  em dashes and the Run control is disabled with its reason. Boundary figures
  are real, because they come from a real parse of a real file
- Deployment configuration for a hosted environment
