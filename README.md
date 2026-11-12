# CanopyLens

**Satellite-powered tree crown detection and canopy analysis.**

Upload high-resolution aerial or satellite imagery, optionally supply a forest boundary as KML/KMZ, and get individual tree crowns detected, counted, measured, and exportable as CSV or GeoJSON — with an explicit account of how much to trust the result.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![Node.js 20+](https://img.shields.io/badge/node-20+-blue.svg)](https://nodejs.org/)

---

## What it does

- **Detects individual tree crowns** in imagery and counts them
- **Reports per-crown area, total canopy area, analysed area, and canopy coverage %**
- **Labels confidence per crown** — model score (DeepForest) or heuristic score (classical CV), never conflating the two
- **Parses KML, KMZ, and GeoJSON boundaries**, draws them on the map, and reports the area they enclose in square metres
- **Restricts analysis to a boundary** and excludes detections outside it
- **Exports results as CSV and GeoJSON**
- **Says clearly when imagery cannot support a physical-area estimate** — no guessing, no fabricated numbers
- **Runs as a background job** with real, pollable, tile-based progress
- **Serves every figure over HTTP** with CSV and GeoJSON downloads
- **Draws crowns on a map**, labels them, and reports one in detail when clicked

---

## Quick start

### Local development

```bash
# Backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
pip install -e ./ml

# Frontend
cd frontend && npm install && cd ..

# Environment files
cp .env.example .env
cp frontend/.env.example frontend/.env

# Run both servers
./scripts/dev.sh        # macOS / Linux
# or
scripts\dev.ps1         # Windows
```

Then open **<http://localhost:5173>**. The Vite dev server proxies `/api` to the backend, so no CORS configuration is needed in development.

### With Docker

```bash
docker compose up --build
```

- Web: <http://localhost:8080>
- API: <http://localhost:8000/api/health>

---

## Using the interface

1. **Drop imagery** — GeoTIFF, PNG, or JPEG. The file is validated against the server's format and size limits before upload.
2. **Drop a boundary** (optional) — KML, KMZ, or GeoJSON. It is parsed and measured server-side the moment it lands and drawn on the map.
3. **Adjust settings** — confidence threshold, minimum crown area, and detection engine. Defaults come from `/api/capabilities`.
4. **Run the analysis** — the upload bar tracks bytes sent, then the job bar tracks tiles completed through `Uploading` → `Processing` → `Post-processing` → `Complete`. Neither bar advances on a timer.
5. **Read the result** — crowns are drawn over the map at their real position. Click one to inspect its ID, confidence, area, and centroid.
6. **Download** the crowns as CSV or GeoJSON. Both come from the backend, so the file and the screen can never disagree.

Imagery without a coordinate system works too: crowns are drawn over the uploaded picture itself in the **Imagery** view, and square-metre figures are left blank rather than guessed.

---

## API

Interactive documentation is at **`/docs`**, generated from the code.

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Liveness: status, version, environment |
| `GET /api/capabilities` | What this server can do: engines, limits, defaults, supported formats |
| `POST /api/analyze` | Upload imagery and an optional boundary; start a background job |
| `GET /api/jobs/{job_id}` | Status, stage, and tile-based progress of a job |
| `POST /api/jobs/{job_id}/cancel` | Stop an analysis that has not finished |
| `GET /api/results/{job_id}` | The measured result once the job is completed |
| `GET /api/results/{job_id}/csv` | The crowns as CSV download |
| `GET /api/results/{job_id}/geojson` | The crowns as GeoJSON download |
| `POST /api/boundary/preview` | Parse and measure a KML, KMZ, or GeoJSON boundary |
| `GET /api/health/ready` | Readiness: whether this instance can actually do the work |
| `GET /api/metrics` | Prometheus counters (requires API key when configured) |

### Submit an analysis

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -F image=@scene.tif \
  -F boundary=@stand.kml \
  -F confidence_threshold=0.30 \
  -F min_crown_area_m2=1.0 \
  -F engine=auto
```

Returns `202 Accepted` with a `job_id`. Poll `GET /api/jobs/{job_id}` for progress, then collect `GET /api/results/{job_id}` when complete.

---

## How detection works

### The models

| Engine | What it is | Confidence | Needs |
|--------|------------|------------|-------|
| **classical** | Excess Green vegetation index, then a distance-transform watershed to separate touching crowns | Heuristic plausibility score from greenness, circularity, and solidity | Core requirements only |
| **deepforest** | [DeepForest](https://deepforest.readthedocs.io/) — RetinaNet pretrained on hand-annotated NEON airborne RGB imagery | Model score from the trained detector | Optional `requirements-ml.txt` (~2.5 GB) |

`engine="auto"` uses DeepForest when it is genuinely installed and working, and falls back to the classical engine otherwise. If neither can run, the pipeline raises `engine_unavailable` — it never substitutes fabricated detections.

DeepForest predicts bounding boxes. A box overstates canopy area by roughly a quarter, so each box is refined into a crown outline by thresholding the vegetation index inside it. Boxes that cannot be refined keep their rectangle and are flagged, so the overestimate is visible rather than hidden.

### The processing workflow

1. **Open the raster** with rasterio, preserving CRS, transform, and nodata. Fewer than three bands is refused.
2. **Choose the measurement CRS** once, from the raster's own footprint — a UTM zone for a normal scene, a local equal-area projection when too wide for a single zone.
3. **Convert the minimum-crown-area setting from m² into pixels** by projecting one actual pixel and measuring it.
4. **Tile** if the raster exceeds 4 MP. Tiles are 800 px with 160 px (20%) overlap, read window by window so the full raster is never loaded into memory.
5. **Detect per tile**, then offset each tile's local coordinates into the full-raster pixel grid.
6. **Deduplicate** crowns found in overlapping tiles using non-maximum suppression on polygon IoU.
7. **Filter** by confidence, then by minimum area.
8. **Georeference and measure** pixel polygons through the affine transform into a projected CRS where area is taken.
9. **Clip to the boundary** if one was supplied. A crown belongs to the boundary when its centroid falls inside it.
10. **Summarise** count, canopy area, analysed area, coverage %, and confidence distribution.

### How the numbers are defined

- **Total canopy area** is the area of the **union** of crown polygons clipped to the analysed region, not the sum of crown areas. Overlapping crowns cover one patch of ground; summing would let coverage exceed 100%.
- **Analysed area** is the imagery footprint, clipped to any boundary, with nodata excluded.
- **Boundary area is not analysed area.** The boundary encloses whatever was drawn; the analysed area is only the part the imagery reaches. Coverage divides by the analysed area.
- **Area is never computed from degrees.** Every area is measured after reprojecting into a projected, metre-based CRS.

---

## Limits and honesty

CanopyLens is an imagery-based estimator, not a survey instrument. These limits are displayed in the app itself:

- Overlapping crowns in closed-canopy stands are frequently merged or split. Counts in dense forest are estimates.
- Dense tropical or mixed-age forest is the hardest case and the least reliable.
- Shadows from terrain, tall neighbours, or a low sun angle hide crowns and create false edges.
- Imagery coarser than about 0.5 m/pixel cannot resolve individual crowns.
- Very small or young trees fall below the detection floor and are missed.
- Crowns clipped by the image or boundary edge have their area underestimated.
- Non-tree vegetation — shrubs, hedgerows, dense crops, green roofs — can be detected as a crown.
- Domain shift: pretrained models were trained on specific regions, sensors, and seasons. Accuracy on unfamiliar imagery is unknown, not merely lower.
- Confidence means different things per engine. Only DeepForest's score comes from a trained detector.

Where a number cannot be computed honestly, it is not computed. Imagery without geospatial metadata yields pixel-based figures explicitly labelled as such.

---

## Measuring quality

`app.validate` scores detections against hand-annotated crowns:

```bash
cd backend
python -m app.validate --truth crowns.geojson --imagery scene.tif --engine deepforest
```

No ground-truth dataset ships with this repository. The dataset is yours to bring and is specific to your sensor, season, and region.

---

## Deployment

Choose one of three deployment paths:

### Docker Compose (any host)

```bash
docker compose up --build
```

Complete stack on any host that runs containers. Uploads and results persist in the `canopylens-data` volume.

### Render (API) + Vercel (frontend)

Two committed manifests describe the split deployment:

- [`render.yaml`](render.yaml) — API on Render (Docker blueprint, single instance)
- [`vercel.json`](vercel.json) — frontend on Vercel

1. Deploy the API on Render via Blueprint.
2. Set `CANOPYLENS_CORS_ORIGINS` to the frontend's origin and redeploy.
3. Deploy the frontend on Vercel, setting `VITE_API_BASE_URL` to the Render URL.

### Vercel Services (single project)

`vercel.json` defines two services in one Vercel project:

| Service | What it is | Public path |
|---------|------------|-------------|
| `backend` | FastAPI API | `/api/*` |
| `frontend` | Vite-built React SPA | `/*` (catch-all) |

```bash
vercel link
vercel --prod
```

Set `VITE_API_BASE_URL` (optional) and `VITE_API_KEY` (if the backend requires keys) in the Vercel dashboard.

> **Vercel serverless notes:** The backend runs as a function, so long analyses may hit function timeouts (10s Hobby / 900s Pro), and the SQLite job store is ephemeral — jobs do not survive redeploys. For long-running background jobs and persistent job history, use Render or Docker Compose.

---

## Environment variables

### Backend (`CANOPYLENS_*`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `CANOPYLENS_APP_NAME` | `CanopyLens API` | Display name |
| `CANOPYLENS_ENVIRONMENT` | `development` | `development` or `production` |
| `CANOPYLENS_LOG_LEVEL` | `INFO` | Python logging level |
| `CANOPYLENS_HOST` | `0.0.0.0` | Bind interface |
| `CANOPYLENS_PORT` | `8000` | Bind port |
| `CANOPYLENS_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Allowed browser origins |
| `CANOPYLENS_DATA_DIR` | `backend/.data` | Uploads and results directory |
| `CANOPYLENS_MAX_UPLOAD_MB` | `512` | Per-file imagery upload limit |
| `CANOPYLENS_MAX_CONCURRENT_ANALYSES` | `2` | Analyses running at once |
| `CANOPYLENS_MAX_QUEUED_ANALYSES` | `20` | Queue limit before `queue_full` |
| `CANOPYLENS_API_KEYS` | *(empty)* | Comma-separated API keys; empty = open |
| `CANOPYLENS_RATE_LIMIT_PER_MINUTE` | `600` | Requests per minute per caller |
| `CANOPYLENS_TRUSTED_PROXY_HOPS` | `0` | Reverse proxies in front |
| `CANOPYLENS_TILE_SIZE` | `800` | Tile edge in pixels |
| `CANOPYLENS_TILE_OVERLAP` | `160` | Tile overlap in pixels |
| `CANOPYLENS_DEFAULT_CONFIDENCE` | `0.30` | Default confidence threshold |
| `CANOPYLENS_DEFAULT_MIN_CROWN_AREA_M2` | `1.0` | Default minimum crown area (m²) |

### Frontend (`VITE_*`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `VITE_API_BASE_URL` | *(empty)* | API base URL; empty = same origin |
| `VITE_API_KEY` | *(empty)* | API key sent as `X-API-Key` |
| `VITE_PROXY_TARGET` | `http://127.0.0.1:8000` | Vite dev proxy target |
| `VITE_BASEMAP_STYLE_URL` | *(empty)* | Optional custom MapLibre style URL |

---

## Project status

The project is **complete and deployable**. Everything planned has been implemented, tested, and is working:

| Area | State |
|------|-------|
| Detection engines | classical CV baseline and DeepForest ✅ |
| Raster pipeline | windowed reading, overlapping tiles, NMS deduplication ✅ |
| Geospatial layer | CRS, KML/KMZ, reprojection, boundary clipping ✅ |
| Export | CSV and GeoJSON from real analysis ✅ |
| Boundary workflow | parsing, repair, preview endpoint, map drawing ✅ |
| Analysis endpoints | upload, background jobs, progress, results, downloads ✅ |
| Results and interaction | crowns on map, click-to-inspect, metrics, downloads ✅ |
| Deployment | Docker Compose, Render blueprint, Vercel project, CI ✅ |

**Test suite:** 508 backend, 57 browser tests — passing.

**CI:** lint, tests, ML engine, frontend build, both container images on every push.

---

## What comes next (separate projects)

These are not more of the same — each is its own project:

- **Ground-truth validation** — score detections against hand-annotated crowns with `app.validate`. No dataset ships with this repository.
- **Shared job store** — jobs live in one SQLite file per instance; surviving a host change needs a shared database.
- **Species or height estimation** — a different inference problem beyond crown detection alone.

---

## Contributing

Contributions are welcome. Please open an issue to discuss proposed changes before submitting a pull request.

---

## License

MIT — see [LICENSE](LICENSE).
