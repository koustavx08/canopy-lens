# CanopyLens

**Satellite-powered tree crown detection and canopy analysis.**

Upload high-resolution aerial or satellite imagery, optionally supply a forest
boundary as KML/KMZ, and get individual tree crowns detected, counted, measured,
and exportable as CSV or GeoJSON — with an explicit account of how much to trust
the result.

---

## Project status

**The detection pipeline is implemented and tested. It is not yet wired to the
HTTP API or the user interface.**

| Area | State |
| --- | --- |
| Backend service, config, typed errors, capability reporting | Working |
| Detection engines: classical CV baseline, DeepForest | Working |
| Raster reading, tiling, overlap deduplication | Working |
| CRS handling, reprojection, area in m2, KML/KMZ/GeoJSON clipping | Working |
| `analyze_image()`, `export_csv()`, `export_geojson()` | Working |
| Test suite (244 tests) | Passing |
| HTTP upload / analysis / export endpoints | **Not built yet** |
| Frontend wired to real results, crown rendering, click-to-inspect | **Not built yet** |

The pipeline can be driven directly today:

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

Until the endpoints exist, **Run analysis in the browser is still disabled and
says why**. No part of the UI shows an invented tree count, area or coverage
figure to fill that gap - metrics read as an em dash until a real analysis
produces them.

---

## What it does

- Detects individual tree crowns in imagery and counts them
- Reports per-crown area, total canopy area, analysed area and canopy coverage %
- Reports a confidence value per crown, labelled with what kind of score it is
- Clips detections to a KML/KMZ/GeoJSON boundary
- Exports results as CSV and GeoJSON
- Says clearly when the imagery cannot support a physical-area estimate

Still to come: the HTTP endpoints and the browser interface that put the above
in front of a user - crown polygons on the map, click-to-inspect, download
buttons.

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
   clipped region, so coverage stays honest.
10. **Summarise**: count, total canopy area, analysed area, coverage %, mean and
    median crown area, and a confidence distribution.

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
| `CANOPYLENS_MAX_UPLOAD_MB` | `512` | Per-file upload limit; larger requests get HTTP 413 |
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

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Liveness: status, version, environment |
| `GET /api/capabilities` | What this running process can actually do: installed dependency versions, per-engine availability with reasons, processing limits, detection defaults, supported formats |
| `GET /docs` | Interactive OpenAPI documentation |

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
5. **HTTP endpoints** - upload, analysis, progress reporting and download,
   mapping the pipeline's typed errors onto the existing error codes.
6. **Results and interaction** - crown polygons on the map, click-to-inspect
   (Crown ID, confidence, area, centroid), the full metrics panel.
7. **Deployment** - frontend on Vercel, backend on Render or Fly.

---

## License

MIT — see [LICENSE](LICENSE).
