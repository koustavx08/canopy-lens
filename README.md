# CanopyLens

**Satellite-powered tree crown detection and canopy analysis.**

Upload high-resolution aerial or satellite imagery, optionally supply a forest
boundary as KML/KMZ, and get individual tree crowns detected, counted, measured,
and exportable as CSV or GeoJSON — with an explicit account of how much to trust
the result.

---

## Project status

**The backend is complete: imagery goes in over HTTP, measured crowns come
back out, and both exports download. What remains is the browser interface that
puts it in front of a user.**

| Area | State |
| --- | --- |
| Backend service, config, typed errors, capability reporting | Working |
| Detection engines: classical CV baseline, DeepForest | Working |
| Raster reading, tiling, overlap deduplication | Working |
| CRS handling, reprojection, area in m2 | Working |
| KML/KMZ/GeoJSON parsing, repair, measurement, clipping | Working |
| `POST /api/boundary/preview` - parse, measure and return a boundary | Working |
| Boundary drawn on the map, with its area, the moment it is dropped | Working |
| `analyze_image()`, `export_csv()`, `export_geojson()` | Working |
| `POST /api/analyze` - upload, validate, run, track | Working |
| `GET /api/jobs/{id}` - status, stage, tile-based progress | Working |
| `GET /api/results/{id}` and its CSV and GeoJSON downloads | Working |
| Test suite (404 tests) | Passing |
| Crown rendering and click-to-inspect in the map | **Not built yet** |
| Frontend wired to the analysis endpoints | **Not built yet** |

The whole pipeline is reachable over HTTP:

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -F image=@scene.tif -F boundary=@stand.kml
# {"job_id":"fba1ea...","status":"queued","status_url":"/api/jobs/fba1ea...", ...}

curl http://127.0.0.1:8000/api/jobs/fba1ea...      # progress while it runs
curl http://127.0.0.1:8000/api/results/fba1ea...   # the measured result
```

It can equally be driven directly, with no server:

```python
from app.services.analysis import analyze_image
from app.services.export import export_csv, export_geojson

result = analyze_image(
    "scene.tif",
    boundary_path="stand.kml",       # optional
    confidence_threshold=0.30,
    min_crown_area_m2=1.0,
    engine="auto",
)
print(result.summary.tree_count, result.summary.canopy_coverage_pct)
```

Boundaries are end-to-end in the browser today: drop a KML, KMZ or GeoJSON and
it is parsed server-side, drawn on the map, and reported with the area it
encloses in square metres.

The detection metrics are not yet wired to the new endpoints, so **Run analysis
in the browser is still disabled and says why**. No part of the UI shows an
invented tree count, area or coverage figure to fill that gap - those metrics
read as an em dash until a real analysis produces them.

---

## What it does

- Detects individual tree crowns in imagery and counts them
- Reports per-crown area, total canopy area, analysed area and canopy coverage %
- Reports a confidence value per crown, labelled with what kind of score it is
- Parses a KML, KMZ or GeoJSON boundary, draws it on the map, and reports the
  area it encloses in square metres
- Restricts the analysis to that boundary and excludes detections outside it
- Exports results as CSV and GeoJSON
- Says clearly when the imagery cannot support a physical-area estimate

- Runs the whole of that as a background job with real, pollable progress
- Serves every figure above over HTTP, with CSV and GeoJSON downloads

Still to come: the parts of the interface that depend on those endpoints -
crown polygons on the map, click-to-inspect, download buttons.

---

## How detection works

### The models

| Engine | What it is | Confidence | Needs |
| --- | --- | --- | --- |
| `classical` | Excess Green vegetation index, then a distance-transform watershed to separate touching crowns | **Heuristic** plausibility score from greenness, circularity and solidity | Core requirements only |
| `deepforest` | [DeepForest](https://deepforest.readthedocs.io/) - a RetinaNet pretrained on hand-annotated NEON airborne RGB imagery of North American forests | **Model** score from the trained detector | Optional `requirements-ml.txt` (~2.5 GB) |

`engine="auto"` uses DeepForest when it is genuinely installed and working, and
falls back to the classical engine otherwise. If neither can run, the pipeline
raises `engine_unavailable` - it never substitutes fabricated detections.

The two confidence values are **not the same kind of number**, and the API says
which is which in `confidence_kind`. The classical engine has no trained
weights, so its score is a documented ranking heuristic, not a calibrated
probability. It is used for thresholding and for choosing a winner during
deduplication, both of which need only a consistent ordering.

DeepForest predicts bounding *boxes*. A box overstates canopy area by roughly a
quarter - a circle inscribed in a square covers pi/4 of it - so each box is
refined into a crown outline by thresholding the vegetation index inside it.
Boxes that cannot be refined keep their rectangle and are flagged, so the
overestimate is visible rather than hidden.

### The processing workflow

1. **Open the raster** with rasterio, preserving CRS, transform and nodata.
   Fewer than three bands is refused: crowns are found from the relationship
   between red, green and blue, which one band cannot express.
2. **Choose the measurement CRS** once, from the raster's own footprint. A UTM
   zone for a normal scene; a local equal-area projection when the extent is
   too wide for a single zone. A source CRS already projected in metres is kept
   as it is.
3. **Convert the minimum-crown-area setting from m2 into pixels**, by projecting
   one actual pixel and measuring it. This is the only place the user's metric
   units meet the engines' pixel units before detection.
4. **Tile**, if the raster exceeds `MAX_PIXELS_WITHOUT_TILING` (4 MP). Tiles are
   800 px with 160 px (20%) overlap, read window by window, so the full raster
   is never loaded into memory. Edge tiles are shifted back inside the raster
   rather than truncated.
5. **Detect per tile**, then offset each tile's local coordinates into the
   full-raster pixel grid.
6. **Deduplicate.** Overlap means a crown on a seam is found by both
   neighbours. Non-maximum suppression on polygon IoU, plus a containment rule
   for the case where one detection sits inside a larger merged blob, keeps the
   highest-confidence polygon per cluster.
7. **Filter** by confidence, then by minimum area.
8. **Georeference and measure.** Pixel polygons go through the affine transform
   into the raster CRS, then into the measurement CRS, where area is taken.
   Invalid rings are repaired first; anything unrepairable is dropped rather
   than contributing a meaningless area.
9. **Clip to the boundary**, if one was supplied. The KML polygon is reprojected
   *into the raster CRS* - the imagery is never resampled - and crowns whose
   centroid falls outside it are excluded. The analysed area becomes the
   clipped region, so coverage stays honest. See
   [The boundary workflow](#the-boundary-workflow).
10. **Summarise**: count, detected crown area, analysed area, coverage %, mean
    and median crown area, and a confidence distribution. The boundary's own
    area is reported separately, because it is not the analysed area.

### How the numbers are defined

- **Total canopy area** is the area of the **union** of crown polygons clipped
  to the analysed region, not the sum of crown areas. Overlapping crowns cover
  one patch of ground, and summing would let coverage exceed 100%. The sum is
  reported separately as `sum_crown_area_m2`, since that is what adding up the
  CSV's area column gives.
- **Analysed area** is the imagery footprint, clipped to any boundary, with
  nodata excluded. A mosaic clipped to an irregular shape is stored in a
  rectangular file with blank corners, and counting those as analysed ground
  would understate coverage.
- **Boundary area is not analysed area.** The boundary encloses whatever the
  user drew; the analysed area is only the part of it the imagery reaches.
  Coverage divides by the second. Both are reported, labelled, and exported.
- **Area is never computed from degrees.** Latitude and longitude are angles;
  `shapely.area` on a WGS84 polygon returns square degrees. Every area is
  measured after reprojecting into a projected, metre-based CRS. Web Mercator
  is deliberately not used - it is conformal, not equal-area, and inflates area
  by 1/cos^2(latitude).
- **Imagery without georeferencing still works**, but every square-metre field
  is empty rather than guessed. Pixel areas are reported because they are true
  regardless of metadata, coverage is computed in pixel space, and the result
  carries a warning saying exactly why no areas are available.

---

## The boundary workflow

A forest boundary is optional. When one is supplied it does three jobs: it
tells the user where their analysis will happen, it restricts the analysis to
that ground, and it gives the coverage percentage an honest denominator.

### What happens to the file

1. **Read.** `.kml`, `.kmz`, `.geojson` and `.json` are accepted. KMZ is a
   zipped KML and is unzipped in place. KML is parsed directly from its XML
   rather than through GDAL's KML driver, which is optional in a GDAL build and
   would make boundary support work on one machine and fail on another.
2. **Extract every area.** Polygons, multi-geometries, inner boundaries
   (clearings), and - where a tool exported an area as a closed path - bare
   `LinearRing` and `LineString` elements.
3. **Repair, then merge.** Each polygon is repaired individually *before* the
   union. A hand-drawn boundary that crosses itself is a bow-tie, and
   `unary_union` raises on some of them; repaired first, one bad ring costs
   that ring rather than the whole file. Polygons that touch merge into one
   part; polygons that do not stay separate.
4. **Establish the CRS.** KML is WGS84 by the OGC specification and GeoJSON is
   WGS84 by RFC 7946, so this is asserted rather than guessed from the size of
   the numbers. The exception is a GeoJSON carrying the pre-RFC 7946 `crs`
   member, which QGIS and ArcGIS still emit: that declaration is honoured, with
   a warning, because reading an easting of 500000 as a longitude would put the
   stand in the Gulf of Guinea.
5. **Measure.** The boundary is reprojected into a projected, metre-based CRS
   and its area taken there. During an analysis that CRS is the one the imagery
   is measured in, so the boundary area and the analysed area are figures in
   the same projection rather than two numbers that merely look comparable.
6. **Reproject into the raster's CRS.** The boundary moves to the imagery, not
   the other way round, so the raster is never resampled and no pixel value is
   altered before detection.
7. **Clip.** A crown belongs to the boundary when its centroid does. Requiring
   the whole polygon to be inside would drop every tree on the forest edge;
   accepting any overlap would pull in trees that are mostly outside it.
8. **Draw.** `POST /api/boundary/preview` returns the geometry in WGS84 for the
   map, so the outline the user sees is the same geometry the analysis will
   clip to - not a separate browser-side approximation of it.

### Three areas, three meanings

This is the distinction the whole section exists for. They are different
numbers and the application never uses one where it means another.

| Figure | What it measures | Where it comes from |
| --- | --- | --- |
| **Boundary area** | The ground the uploaded file encloses, clearings excluded | The boundary file alone. It exists whether or not any imagery covers it |
| **Analysed area** | The part of that ground the imagery actually covers, with nodata removed | Imagery footprint ∩ boundary, scaled by the valid-data fraction |
| **Detected crown area** | The ground covered by crowns inside the analysed area | The union of crown polygons, clipped to the analysed region |

```text
canopy coverage % = detected crown area / analysed area * 100
```

The denominator is the **analysed area**, never the boundary area. A boundary
twice the size of the image would otherwise halve the coverage figure while
looking entirely reasonable. When the imagery does not cover the whole
boundary the result says so:

```text
The imagery covers 49.6% of the boundary (22,100 m2 of 44,600 m2).
Results describe only the part of the boundary the image reaches.
```

### Edge cases and what the user is told

Every one of these returns a message naming what to change, not a stack trace
and not an empty map.

| Case | Behaviour |
| --- | --- |
| Invalid XML or JSON | `invalid_boundary`, quoting the parser's own position |
| Empty file (0 bytes) or empty document | Reported as empty before parsing is attempted |
| A KML of pins or paths only | Explained as "use the polygon tool", not "no polygon found" |
| A polygon whose points are collinear | Refused as zero-area rather than measured as 0 |
| A self-intersecting ring | Repaired; the area is taken from the repaired geometry |
| Several polygons | Kept as separate parts, counted, and reported in the warnings |
| Touching polygons | Merged into one part, since they describe one area |
| Holes (clearings) | Subtracted from the boundary area and from the analysed area |
| Boundary larger than the imagery | Allowed; the shortfall is measured and warned about |
| A part with no imagery over it | Counted in the boundary area, reported as not analysed |
| Boundary nowhere near the imagery | Refused, with both extents in the error detail |
| Boundary on non-georeferenced imagery | Refused: there is no way to say which pixels it covers |
| Projected GeoJSON (`crs` member) | Honoured and reprojected, with a warning |
| KMZ with no KML inside, or a corrupt archive | Refused, with advice on what to upload instead |

---

## Requirements

| Tool | Version | Notes |
| --- | --- | --- |
| Python | 3.12+ | 3.12 is what the project is developed and tested against |
| Node.js | 20+ | 22 recommended; ships with npm |
| Docker | 24+ | Optional — only for the container workflow |

No API keys, accounts, or cloud credentials are needed. The basemaps are
key-free.

---

## Setup

### Scripted (recommended)

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

macOS / Linux:

```bash
./scripts/setup.sh
```

This creates `.venv`, installs backend and dev dependencies, installs the
`canopylens_ml` package in editable mode, installs frontend packages, and
creates `.env` and `frontend/.env` from their examples.

To also install the optional deep-learning engine (CPU torch + DeepForest,
roughly 2.5 GB), add `-WithDeepLearning` on Windows or `--with-deep-learning` on
macOS/Linux. The app runs fine without it.

### Manual

```bash
# Backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
pip install -e ./ml

# Frontend
cd frontend && npm install && cd ..

# Environment files (optional; defaults work for local development)
cp .env.example .env
cp frontend/.env.example frontend/.env
```

Optional deep-learning engine:

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r backend/requirements-ml.txt
```

`requirements-ml.txt` pins `albumentations<2.0`. DeepForest 1.5 imports
`albumentations.functional`, which albumentations 2.x removed; without the pin
the package installs cleanly and then fails on first use. The DeepForest
engine's availability check imports `deepforest.main` rather than the bare
package precisely so that this class of breakage is reported as "engine
unavailable, here is why" instead of crashing mid-analysis.

The pretrained weights (~130 MB) download from Hugging Face on first use and
are cached, so the first DeepForest run needs network access.

---

## Running in development

Both servers, one command:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1    # Windows
```

```bash
./scripts/dev.sh                                             # macOS / Linux
```

Then open **<http://localhost:5173>**. The Vite dev server proxies `/api` to the
backend, so the browser talks to a single origin and CORS never applies in
development.

Individually:

```bash
# API  -> http://127.0.0.1:8000  (interactive docs at /docs)
python -m uvicorn app.main:app --app-dir backend --reload --port 8000

# Web  -> http://localhost:5173
cd frontend && npm run dev
```

### Other commands

```bash
pytest                       # backend + geospatial tests
cd frontend && npm run build # type-check and production build
cd frontend && npm run lint  # type-check only
```

---

## Running with Docker

```bash
docker compose up --build
```

- Web: <http://localhost:8080>
- API: <http://localhost:8000/api/health>

The web container serves the built bundle through nginx and proxies `/api` to
the API container. Uploads and results persist in the `canopylens-data` volume.

Individual images:

```bash
docker build -f backend/Dockerfile -t canopylens-api .        # context = repo root
docker build -f frontend/Dockerfile -t canopylens-web frontend
```

The backend image deliberately excludes the deep-learning extras — torch plus
pretrained weights add about 2.5 GB, which exceeds the image budget of most free
container hosts. The API runs without them and `/api/capabilities` reports
truthfully which engines are usable.

---

## Environment variables

Every backend variable is optional; the default shown is what applies when it is
unset. Full annotated list: [`.env.example`](.env.example).

### Backend (`.env` at the repository root, prefix `CANOPYLENS_`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `CANOPYLENS_APP_NAME` | `CanopyLens API` | Display name reported by `/api/health` |
| `CANOPYLENS_ENVIRONMENT` | `development` | `development` or `production`; reported to the client |
| `CANOPYLENS_LOG_LEVEL` | `INFO` | Python logging level |
| `CANOPYLENS_HOST` | `0.0.0.0` | Interface uvicorn binds to |
| `CANOPYLENS_PORT` | `8000` | Port uvicorn binds to |
| `CANOPYLENS_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated allowed browser origins |
| `CANOPYLENS_DATA_DIR` | `backend/.data` | Where uploads and results are written; relative paths resolve from the repo root |
| `CANOPYLENS_MAX_UPLOAD_MB` | `512` | Per-file imagery upload limit; larger requests get HTTP 413 |
| `CANOPYLENS_MAX_BOUNDARY_UPLOAD_MB` | `32` | Separate, much lower limit for boundary files — a KML is text describing an outline, and the imagery allowance has no business applying to it |
| `CANOPYLENS_TILE_SIZE` | `800` | Tile edge length in pixels |
| `CANOPYLENS_TILE_OVERLAP` | `160` | Overlap between neighbouring tiles, in pixels |
| `CANOPYLENS_MAX_PIXELS_WITHOUT_TILING` | `4000000` | Rasters smaller than this are processed in one pass |
| `CANOPYLENS_DEFAULT_CONFIDENCE` | `0.30` | Initial confidence threshold offered to the UI |
| `CANOPYLENS_DEFAULT_MIN_CROWN_AREA_M2` | `1.0` | Initial minimum crown-area filter, m² |
| `CANOPYLENS_DEFAULT_ENGINE` | `auto` | `auto` picks the best engine that actually reports itself available |

### Frontend (`frontend/.env`, prefix `VITE_`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_API_BASE_URL` | *(empty)* | Base URL of the API. Leave empty in development and in the Docker stack, where `/api` is proxied on the same origin. Set it when the API is on another host, e.g. `https://canopylens-api.onrender.com` |
| `VITE_PROXY_TARGET` | `http://127.0.0.1:8000` | Where the Vite dev server proxies `/api` (development only) |
| `VITE_BASEMAP_STYLE_URL` | *(empty)* | Optional MapLibre style URL replacing the built-in key-free basemaps |

`VITE_*` values are compiled into the bundle at build time, so changing one
requires a rebuild — for Docker, pass it as `--build-arg`.

---

## Sample data

The app needs real imagery; no sample raster is bundled, because the useful
public datasets are large and carry their own licences.
[`sample_data/README.md`](sample_data/README.md) lists direct download sources
(NEON airborne RGB at 10 cm/pixel is the best first choice), what resolution and
format actually work, how to crop a large tile to a quick test file, and how to
check whether a file carries usable georeferencing.

---

## API

Interactive documentation is at **`/docs`**, generated from the code, so the
request and response shapes there are the ones the server actually enforces.

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Liveness: status, version, environment. Also at `/api/health`, because orchestrators look for it at the root |
| `GET /api/capabilities` | What this running process can actually do: installed dependency versions, per-engine availability with reasons, processing limits, detection defaults, supported formats |
| `POST /api/analyze` | Upload imagery and an optional boundary, start an analysis, get a job id |
| `GET /api/jobs/{job_id}` | Status, stage and progress of that job |
| `GET /api/results/{job_id}` | The measured result once the job has completed |
| `GET /api/results/{job_id}/csv` | The crowns as CSV, as a browser download |
| `GET /api/results/{job_id}/geojson` | The crowns as GeoJSON, as a browser download |
| `POST /api/boundary/preview` | Parse a KML, KMZ or GeoJSON boundary and return it as WGS84 geometry plus its measured area |
| `GET /docs` | Interactive OpenAPI documentation |

### Running an analysis

An analysis is not a request-shaped piece of work. A large mosaic takes
minutes, during which a synchronous endpoint would give the browser no progress
and a reverse proxy every excuse to time out and discard work that had nearly
finished. So the flow is submit, poll, collect.

**1. Submit.** `multipart/form-data`; `image` is required and everything else
is optional.

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -F image=@scene.tif \
  -F boundary=@stand.kml \
  -F confidence_threshold=0.30 \
  -F min_crown_area_m2=1.0 \
  -F engine=auto
```

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `image` | file | required | `.tif`, `.tiff`, `.geotiff`, `.png`, `.jpg`, `.jpeg` |
| `boundary` | file | none | `.kml`, `.kmz`, `.geojson`, `.json` |
| `confidence_threshold` | float | `CANOPYLENS_DEFAULT_CONFIDENCE` | 0.0-1.0 |
| `min_crown_area_m2` | float | `CANOPYLENS_DEFAULT_MIN_CROWN_AREA_M2` | 0-100000; ignored for imagery with no georeferencing, which has no square metres to measure |
| `engine` | string | `CANOPYLENS_DEFAULT_ENGINE` | `auto`, or a name from `/api/capabilities` |

`202 Accepted`, and nothing countable in it - at this point nothing has been
counted:

```json
{
  "job_id": "fba1ea9fda97493ba1587ac8672c9061",
  "status": "queued",
  "status_url": "/api/jobs/fba1ea9fda97493ba1587ac8672c9061",
  "result_url": "/api/results/fba1ea9fda97493ba1587ac8672c9061"
}
```

**2. Poll.** Cheap enough to call once a second; it deliberately carries no
detections, so a client watching a progress bar is not re-downloading polygons
to learn that nothing has finished yet.

```json
{
  "job_id": "fba1ea...",
  "status": "running",
  "stage": "detecting",
  "progress": 0.42,
  "image_name": "scene.tif",
  "boundary_name": "stand.kml",
  "parameters": { "confidence_threshold": 0.3, "min_crown_area_m2": 1.0, "engine": "auto", ... },
  "created_at": "2026-09-13T15:55:51+00:00",
  "started_at": "2026-09-13T15:55:51+00:00",
  "finished_at": null,
  "processing_time_seconds": null,
  "error": null,
  "result_url": null
}
```

`status` is `queued`, `running`, `completed` or `failed`. `stage` moves through
`reading`, `detecting`, `measuring`, `summarising`.

**`progress` counts tiles completed, not seconds elapsed.** A progress bar that
is really a stopwatch tells the user how long they have waited, which they
already know. It is a measure of work done, and it is not an estimate of time
remaining - the API does not offer one it cannot compute. An image small enough
to be a single tile therefore jumps rather than sweeping, which is the honest
shape of that work.

`processing_time_seconds` is measured from when the analysis *started*, not
from when the job was created: time spent queued behind other work is not time
the pipeline spent on this image.

**3. Collect.** `GET /api/results/{job_id}` once the status is `completed`.
Asking earlier returns `409 job_not_finished` and names the URL to poll; asking
for a job that failed returns the failure's own code and message, exactly as a
synchronous call would have.

```json
{
  "job_id": "fba1ea...",
  "status": "completed",
  "image_metadata": {
    "filename": "scene.tif",
    "width_px": 600, "height_px": 600,
    "georeferenced": true,
    "source_crs": "EPSG:32617",
    "measurement_crs": "EPSG:32617",
    "pixel_area_m2": 0.0625,
    "resolution": [0.25, 0.25],
    "bounds": [500000.0, 4649626.0, 500150.0, 4649776.0]
  },
  "boundary_metadata": {
    "source_format": "KML",
    "part_count": 1,
    "has_holes": false,
    "area_m2": 11250.0,
    "area_within_imagery_m2": 11250.0,
    "imagery_covers_pct": 100.0,
    "detections_excluded": 2,
    "geometry": { "type": "Polygon", "coordinates": [[ ... ]] }
  },
  "summary": {
    "tree_count": 1,
    "total_canopy_area_m2": 140.09,
    "analyzed_area_m2": 11250.0,
    "canopy_coverage_pct": 1.25,
    "coverage_basis": "area",
    "sum_crown_area_m2": 140.09,
    "mean_crown_area_m2": 140.094,
    "confidence_distribution": [ { "lower": 0.5, "upper": 0.6, "count": 1 } ],
    "engine": "deepforest",
    "confidence_kind": "model",
    "excluded_by_boundary": 2,
    "raw_detections": 4,
    "duplicates_removed": 1
  },
  "detections": [
    {
      "id": "crown_00001",
      "confidence": 0.5008,
      "confidence_kind": "model",
      "area_m2": 140.094,
      "pixel_area": 2241.5,
      "centroid_lon": -80.99954435,
      "centroid_lat": 41.99914226,
      "geometry": { "type": "Polygon", "coordinates": [[ ... ]] },
      "geometry_px": { "type": "Polygon", "coordinates": [[ ... ]] }
    }
  ],
  "processing_time": { "seconds": 31.247, "pipeline_seconds": 31.247, "tiles_processed": 1 },
  "warnings": [],
  "limitations": { "notice": "Results are model estimates, not measurements. ...", "items": [ ... ] },
  "downloads": { "csv": "/api/results/fba1ea.../csv", "geojson": "/api/results/fba1ea.../geojson" }
}
```

Three things about that payload are deliberate:

- **`boundary_metadata.area_m2`, `summary.analyzed_area_m2` and
  `summary.total_canopy_area_m2` are three different quantities** and coverage
  divides by the second. See
  [Three areas, three meanings](#three-areas-three-meanings).
- **`limitations` travels with the numbers.** The figure that gets pasted into
  a report is the one from this response, so the caveat is part of it rather
  than a line in the documentation. `limitations` holds the standing limits of
  the method; `warnings` holds what was specific to *this* image and boundary.
- **`geometry` is null and every `*_m2` field is null for imagery with no
  georeferencing.** `geometry_px` and the pixel figures are still real, because
  they are measured from the image itself. Nothing is scaled by an assumed
  ground resolution to make the response look complete.

**4. Download.** Both exports are generated from the stored result on request,
with a `Content-Disposition` filename:

```bash
curl -OJ http://127.0.0.1:8000/api/results/fba1ea.../csv
curl -OJ http://127.0.0.1:8000/api/results/fba1ea.../geojson
```

### Where jobs live

In memory, in the API process. Results are useful for as long as the tab that
asked for them is open, and a database would mean inventing retention rules,
migrations and cleanup for data whose whole lifetime is a few minutes. The cost
is stated rather than discovered:

- **A restart forgets every job.** An id from a previous run returns 404, and
  says so in the message.
- **Jobs do not survive across worker processes.** Run the API with one worker,
  or move the store to Redis before scaling out.
- **At most 50 jobs are remembered**, oldest finished first. Work still running
  is never evicted to make room.

Uploaded files are deleted as soon as the analysis has read them, so a completed
job leaves nothing on disk.


```bash
curl -F file=@stand.kml http://127.0.0.1:8000/api/boundary/preview
```

```json
{
  "geometry": { "type": "Polygon", "coordinates": [[ ... ]] },
  "area_m2": 44600.0,
  "area_hectares": 4.46,
  "part_count": 1,
  "has_holes": true,
  "source_format": "KML",
  "source_crs": "EPSG:4326",
  "measurement_crs": "EPSG:32617",
  "bounds_wgs84": [-81.0, 41.99864692, -80.99637758, 41.99999798],
  "centroid_wgs84": [-80.99818879, 41.99932245],
  "warnings": []
}
```

`area_m2` is the ground the boundary encloses, measured in `measurement_crs` -
it is not the analysed area and not the canopy area. The upload is read,
measured and discarded within the request; nothing is stored.

### Uploads and safety

- **Size limits are enforced on the bytes read**, not on a `Content-Length` the
  client asserted, so an oversized or lying upload is abandoned mid-stream
  rather than filling the disk and being rejected afterwards. Imagery and
  boundaries have separate ceilings - a KML is text describing an outline, and
  the 512 MB imagery allowance has no business applying to it.
- **The client's filename is never used as a path.** Only its extension
  survives, and only after being matched against an allow-list; the stored name
  is generated. The original is kept for display only, and is what the result
  and both exports report as the source image.
- **Job ids index a dictionary**, never a path, so a traversal attempt is
  simply a key that is not there and answers 404.
- **Nothing uploaded is executed or interpreted** - imagery is opened by
  rasterio and boundaries are parsed as XML or JSON, both as data.
- **CORS is an allow-list** from `CANOPYLENS_CORS_ORIGINS`, not `*`.
- **Stack traces never appear in a response.** An unexpected failure is logged
  in full on the server, where it is useful, and returns a plain 500 saying
  only that the request failed.

Errors are returned as:

```json
{ "error": { "code": "missing_georeference", "message": "...", "detail": {} } }
```

Clients branch on `code`, never on `message`. The codes are listed in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#error-codes-currently-defined).

---

## Architecture at a glance

```
frontend/   React 19 + TypeScript + Vite + Tailwind v4 + MapLibre GL
backend/    FastAPI + rasterio + shapely + pyproj + geopandas
ml/         canopylens_ml — installable detection package, pixel space only
```

Engines return polygons in raster pixel coordinates. Everything involving CRS,
affine transforms, reprojection, and area in square metres lives in the backend
geo layer — one place where a pixel becomes a metre, so one place to get it
right. Full detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Limitations and honesty

This is an imagery-based estimator, not a survey instrument. These limits are
displayed in the app itself, not buried here:

- **Overlapping crowns** in closed-canopy stands are frequently merged into one
  detection or split into several. Counts in dense forest are estimates.
- **Dense tropical or mixed-age forest** is the hardest case and the least
  reliable.
- **Shadows** — from terrain, tall neighbours, or a low sun angle — hide crowns
  and create false edges.
- **Imagery coarser than about 0.5 m/pixel** cannot resolve individual crowns.
  Below that threshold the output describes canopy patches, not trees.
- **Very small or young trees** fall below the detection floor and are missed.
- **Crowns clipped by the image or boundary edge** have their area
  underestimated, because only the visible part is measured.
- **Non-tree vegetation** — shrubs, hedgerows, dense crops, green roofs — is
  visually similar from above and can be detected as a crown.
- **Domain shift**: pretrained crown models were trained on specific regions,
  sensors, and seasons. Accuracy on imagery unlike that training data is
  unknown, not merely lower.
- **Confidence means different things per engine.** The classical engine's
  score is a rule-based plausibility ranking from greenness, circularity and
  solidity - useful for ordering and thresholding, but not a probability that a
  tree is there. Only DeepForest's score comes from a trained detector. The API
  reports which kind you are looking at in `confidence_kind`; do not compare
  the two as if they were the same scale.
- **The classical engine is a baseline, not a peer of the trained model.** It
  exists so the application always has a working path. In closed canopy it
  merges neighbouring crowns more readily than DeepForest does.
- **Crown area is the segmented foliage outline**, which is smaller than the
  true crown wherever foliage is in deep shadow or occluded by a neighbour.
  Measured against synthetic circles the segmentation runs a few per cent
  under the true area.
- **A boundary is taken as drawn.** CanopyLens measures and clips to the
  polygon in the file; it does not check that the polygon is where the forest
  actually is. A boundary traced loosely around a stand will report more
  analysed ground than the stand contains, and the coverage figure follows.
- **Coverage describes the analysed area, not the stand.** Where the imagery
  covers only part of the boundary, the percentage is for the part that was
  photographed. The shortfall is measured and warned about, but the number
  itself cannot stand in for the whole stand.
- **A crown belongs to the boundary when its centre does.** Trees straddling
  the line are counted in full or not at all, so a long, thin boundary through
  dense canopy has more edge error than a compact one.
- **No ground-truth validation is performed.** Nothing this tool outputs is a
  field-verified tree inventory, and it should not be presented as one.

Where a number cannot be computed honestly it is not computed. Imagery without
sufficient geospatial metadata yields pixel-based figures explicitly labelled as
such rather than a square-metre value that looks authoritative and is not.

---

## Roadmap

1. ~~**Detection engines**~~ - classical CV baseline and DeepForest, both
   registered and reporting their availability honestly. **Done.**
2. ~~**Raster pipeline**~~ - windowed reading, overlapping tiles, NMS
   deduplication at tile boundaries. **Done.**
3. ~~**Geospatial layer**~~ - CRS preservation, KML/KMZ parsing and
   reprojection into the raster CRS, boundary clipping, area via a projected
   CRS. **Done.**
4. ~~**Export**~~ - `export_csv()` and `export_geojson()` generated from the
   real analysis. **Done.**
5. ~~**Boundary workflow**~~ - parsing, repair, multi-part and hole handling,
   declared coordinate systems, boundary area in m2, `POST
   /api/boundary/preview`, and the boundary drawn on the map as soon as it is
   dropped. **Done.**
6. ~~**Analysis endpoints**~~ - imagery upload with streamed size limits,
   background jobs with tile-based progress, the full result payload, and CSV
   and GeoJSON downloads, with the pipeline's typed errors mapped onto the
   existing error codes. **Done.**
7. **Results and interaction** - crown polygons on the map, click-to-inspect
   (Crown ID, confidence, area, centroid), the full metrics panel.
8. **Deployment** - frontend on Vercel, backend on Render or Fly.

---

## License

MIT — see [LICENSE](LICENSE).
