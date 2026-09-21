# CanopyLens architecture

This document describes how the system is put together, why it is split the way
it is, and which parts are deliberately not built yet.

> **Status: complete end to end, and deployable.** Engines, tiling,
> deduplication, CRS handling, boundary parsing and measurement, clipping, area
> measurement, export, and the HTTP layer that serves all of it are implemented
> and covered by 417 tests. Imagery goes in over `POST /api/analyze`, progress
> comes back from `GET /api/jobs/{id}`, and the measured result and both
> downloads come back from `GET /api/results/{id}`; the browser drives all of
> that, draws the crowns, and downloads both exports. CI runs lint, the suite,
> the frontend build and both container builds on every push, and the hosted
> configuration is committed (`render.yaml`, `vercel.json`, `docker-compose.yml`).
> What is still absent is listed under [Not built yet](#not-built-yet). Nothing
> in the UI fabricates a figure: every number on screen comes from an analysis
> response, and a control that cannot do real work is disabled with the reason
> shown.

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
    analysis.py       submit, poll, collect; the job endpoints
  services/           domain logic; no FastAPI imports
    raster.py         rasterio reading, tiling, dtype normalisation
    geo.py            CRS selection, reprojection, area in m2 — the only place
                      a pixel becomes a metre
    boundary.py       KML/KMZ/GeoJSON parsing, repair and measurement
    dedup.py          non-maximum suppression across tile seams
    analysis.py       the orchestrator: analyze_image()
    export.py         CSV and GeoJSON
    uploads.py        streamed, size-limited receipt of uploaded files
    jobs.py           the in-memory job register
    results.py        AnalysisResult -> the JSON the API returns
    capabilities.py   what this process can actually do, read from the process
  limitations.py      the standing limits of the method, in one place
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
| `not_found` | 404 | A referenced job, analysis or upload does not exist |
| `invalid_request` | 422 | Request-body validation failed; the message names the field |
| `invalid_parameter` | 422 | A well-formed value naming something that does not exist, such as an unknown engine |
| `job_not_finished` | 409 | A result was asked for before its analysis completed |
| `analysis_failed` | 500 | The pipeline stopped unexpectedly; details are in the server log only |
| `internal_error` | 500 | An unhandled bug. Never carries a stack trace |

Two of those exist to keep three different kinds of "wrong value" apart:
`invalid_request` is a number out of range, `invalid_parameter` is a name that
does not exist, and `engine_unavailable` is a real engine that cannot run in
this process. A client that wants to offer the right correction needs to tell
them apart.

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

## The analysis endpoints

`routers/analysis.py` owns three URLs, `services/jobs.py` owns the state behind
them, and `services/results.py` owns the shape they answer in. The pipeline
itself — `analyze_image` — still knows nothing about HTTP.

### Why a job and not a response

A large mosaic takes minutes. Holding the connection open for it is a bad
bargain in three ways: the browser gets no progress, a reverse proxy may time
out in the middle, and a dropped connection loses work that had nearly
finished. `POST /api/analyze` validates, accepts, schedules and returns 202 with
a job id; the client polls.

Validation happens *before* anything is scheduled. An unsupported extension, an
oversized file or an unknown engine is answered on the spot, rather than
becoming a job that exists only to fail thirty seconds later.

The work runs in FastAPI's background-task threadpool. That is enough for a
single-process deployment and it is the honest ceiling: jobs live in one
process's memory, so running several workers would scatter them. A second
worker is the point at which the store moves to Redis, and nothing else has to
change, because the router only ever talks to `JobStore`.

### Why progress counts tiles

`analyze_image` takes an optional `(stage, fraction)` callback and calls it once
per tile. The fraction is tiles completed, mapped into the middle of the range,
because detection is the only stage whose length depends on the image.

What it is *not* is a timer. A progress bar driven by elapsed time tells the
user how long they have waited, which they already know, and implies a
completion estimate the server cannot compute. The cost of being honest is
visible: a single-tile image jumps from 0.15 to 1.0 rather than sweeping. That
is the real shape of the work.

The callback's exceptions are caught and logged. A broken progress reporter is a
reporting problem, and it must not be able to fail an analysis that has
otherwise succeeded.

### Why the store is a dictionary

Results are useful for about as long as the tab that requested them is open. A
database would mean retention rules, migrations and a cleanup story for data
whose whole lifetime is a few minutes. So the store is an `OrderedDict` behind a
`threading.Lock` — analyses run in worker threads while requests are served on
the event loop, so every read and write takes the lock.

It is bounded at 50 jobs, because an unbounded dictionary of analysis results is
a memory leak with a polite name. Eviction only ever takes *finished* jobs,
oldest first; if everything is still running the store goes over budget and logs
it, since discarding work somebody is waiting for is the worse failure.

`AnalysisResult` is kept as the object the pipeline produced. CSV and GeoJSON
are rendered from it on request rather than written to disk, so there are no
per-job files to clean up later.

### Why analyses run on their own bounded pool

Submitting work to the server's general worker threads would let every upload
start at once — up to the framework's default of forty. Detection is CPU- and
memory-bound, so forty concurrent analyses do not finish any sooner; they make
each one slower, and because every endpoint here is synchronous they take the
same threads the progress polls and the health check need. The failure mode is
the bad one: the service stops answering, the platform restarts it, and every
job in memory dies with it.

So analyses are submitted to a `ThreadPoolExecutor` of
`CANOPYLENS_MAX_CONCURRENT_ANALYSES` threads (default 2). Work above the limit
queues, reported as the `queued` status the job model already had — which is
why `processing_time_seconds` is measured from when an analysis *started*, not
from when it was accepted. The pool is shut down with the application, and by
`atexit` for a process that is killed rather than stopped.

### Why a failed job answers like a synchronous call

`JobError` records the failure's `code`, `message` **and** `status_code`. Asking
for the result of a failed job re-raises it, so a boundary that would have
returned `422 invalid_boundary` inline returns exactly that from
`GET /api/results/{id}`. Flattening every background failure into one generic
500 would throw away the only part the user can act on.

Unexpected exceptions are the exception: they are logged in full and reported as
`analysis_failed` with no internal detail, because a traceback tells the user
nothing they can use and tells an attacker the library versions.

### Why the result carries its own limitations

The figure that gets pasted into a report is the one from the response, so the
caveat travels with it rather than living only in this document.

`limitations` and `warnings` are deliberately separate. `limitations` is the
standing limits of the method, identical on every result and sourced from
`app/limitations.py`. `warnings` is what was true of *this* image and *this*
boundary — no georeferencing, imagery covering half the stand, a repaired
polygon. Merging them would force the reader to work out which was which.

### Why the result names the uploaded file

An upload's own filename is never used as a path: only its extension survives,
and the stored name is generated. That leaves a result identifying the imagery
by a random hex string, which means nothing to the person who uploaded it. So
`AnalysisResult.source_name` carries the display name separately, and it is what
the result payload, the CSV provenance header and the GeoJSON metadata all
report. The path on disk and the name in the report are two different things,
and only one of them is safe to take from the client.

### Upload safety

| Concern | How it is handled |
| --- | --- |
| Oversized upload | Enforced on bytes read, not on `Content-Length`; the stream is abandoned mid-write and the partial file removed |
| Imagery limit applied to a KML | Separate ceiling: `CANOPYLENS_MAX_BOUNDARY_UPLOAD_MB` |
| Path traversal via filename | The filename is never used as a path; the stored name is a UUID plus an allow-listed extension |
| Path traversal via job id | Job ids index a dictionary, so a traversal attempt is a key that is not there — 404 |
| A rejected boundary stranding the image | The image is removed before the rejection propagates |
| Files accumulating | Uploads are deleted once the analysis has read them; results live in memory |
| Arbitrary execution | Nothing uploaded is executed; imagery is opened by rasterio, boundaries parsed as XML or JSON |
| Cross-origin abuse | CORS is an allow-list from `CANOPYLENS_CORS_ORIGINS`, never `*` |
| Information disclosure | A catch-all handler returns a plain 500; the traceback goes to the log |

## Frontend structure

```
frontend/src/
  App.tsx              state owner: capabilities, files, params, results
  components/
    Header.tsx          product identity, backend status
    Sidebar.tsx         upload, boundary, detection settings, progress, run
    MapView.tsx         MapLibre map and pixel view; boundary and crown layers
    ResultsPanel.tsx    metrics, warnings, downloads, method, capabilities
    CrownInspector.tsx  one crown in detail, and keyboard access to all of them
    ConfidenceChart.tsx the score distribution, as plain bars
    Limitations.tsx     permanently visible, not hidden behind a disclosure
    Dropzone.tsx        shared file-input primitive
  lib/
    api.ts              typed fetch wrapper preserving the server's error code
    useAnalysis.ts      submit, poll, collect; owns the waiting
    basemaps.ts         key-free raster basemap styles
    format.ts           number and unit formatting
  types.ts              shared response types mirroring the API payloads
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

### Why the browser polls instead of waiting

`useAnalysis` submits, polls `GET /api/jobs/{id}` once a second, and fetches the
result when the job finishes. That mirrors the backend's own shape and keeps the
one property that matters: nothing in the interface is blocked while an analysis
runs. The upload uses `XMLHttpRequest` rather than `fetch` for a single reason —
it is the only way to observe upload progress, and an indeterminate spinner over
a five-minute upload of a large GeoTIFF tells the user nothing.

The phase shown — `Uploading`, `Processing`, `Post-processing`, `Complete`,
`Error` — is derived from the job's own `stage`, and the bar from bytes sent and
then tiles completed. Neither advances on a timer. A progress bar driven by
elapsed time reports how long the user has waited, which they already know, and
implies a completion estimate the server has not made.

### Why there are two views of the same result

A crown carries two geometries, and which one exists decides what can be shown.
`geometry` is WGS84 and is null for imagery with no coordinate system;
`geometry_px` is measured from the image and is therefore always present.

So the map draws `geometry` over a basemap, and the pixel view draws
`geometry_px` over the uploaded picture in an SVG whose `viewBox` is the image's
own pixel grid — the browser does the scaling, and it stays correct at any window
size. Without the second view, crowns found in a plain PNG could not be shown at
all, and the alternative — assuming a ground resolution so that everything has
map coordinates — would put fabricated positions on screen.

Crown IDs are a MapLibre symbol layer above zoom 17. Below that, a thousand
overlapping numbers is not a label; the inspector names the selected crown in
full regardless.

### Why the inspector has previous and next controls

Clicking a polygon is a mouse-only gesture. Stepping through the crowns from the
keyboard is how the same information is reachable without one, and the map pans
to whichever crown is selected however it was selected.

### Why the exports are fetched rather than linked

Both downloads go through `fetch` and a blob rather than a plain `<a href>`. A
link pointed at a job the server has forgotten downloads the JSON error body as
a file named `crowns.csv`, which the user has to open to discover is broken;
fetching first means a failure is reported as a failure. The file itself is
always generated by the backend — a CSV assembled in the browser would be a
second implementation of the same numbers, and two implementations of one number
eventually disagree.

### Why `types.ts` is not the whole contract

TypeScript will compile happily against a declaration the server no longer
honours; the mismatch surfaces as a blank metric or a map with no crowns on it.
`tests/backend/test_frontend_contract.py` asserts the presence of every key the
interface reads, so a renamed field fails a test instead of quietly emptying a
panel.

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

Both targets are committed rather than described: `render.yaml` builds
`backend/Dockerfile` as a one-instance web service health-checked on
`/api/health`, and `vercel.json` builds the client with its SPA fallback and
cache headers. The one-instance pin is not a cost decision — the job store is
process memory (see [Why the store is a dictionary](#why-the-store-is-a-dictionary)),
so a second instance would field polls for jobs it never ran. Moving the store
to Redis or Postgres is the prerequisite for scaling out, not an afterthought
to it.

`.github/workflows/ci.yml` runs `ruff check`, the full suite, `tsc -b && vite
build`, and a build of both images on every push and pull request. It installs
the core requirements only: the suite treats an absent engine as unavailable
rather than as a failure, so CI both stays fast and exercises the exact
dependency set the deployed image ships.

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
| Upload size limits, filename handling, the startup sweep | `tests/backend/test_uploads.py` |
| Submit/poll/collect, downloads, validation, CORS | `tests/backend/test_analysis_api.py` |
| Job lifecycle, eviction, progress reporting | `tests/backend/test_jobs.py` |
| The error envelope, and that 500s carry no traceback | `tests/backend/test_errors.py` |
| The payload keys the browser reads | `tests/backend/test_frontend_contract.py` |
| Engines, registry, pixel-space primitives | `tests/ml/` |

## Not built yet

Listed so the gap is explicit rather than discovered:

- A job store shared between hosts. The SQLite record survives a restart and is
  shared by every worker on one machine, but two machines have two files; that
  needs a database rather than a local file
- Cancelling a running analysis. The browser can stop watching one, which
  releases the page, but the work on the server runs to completion
- Rendering the uploaded GeoTIFF itself in the browser. The map shows its crowns
  at their real position over a basemap; the pixel view is available only for
  formats the browser can decode
- Automated tests for the frontend. The payload contract is covered from the
  backend side; the React components are not
- Ground-truth validation. Nothing here has been scored against field-verified
  crowns, which is why every figure is labelled an estimate
