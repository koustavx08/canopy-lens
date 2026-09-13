# CanopyLens

**Satellite-powered tree crown detection and canopy analysis.**

Upload high-resolution aerial or satellite imagery, optionally supply a forest
boundary as KML/KMZ, and get individual tree crowns detected, counted, measured,
and exportable as CSV or GeoJSON — with an explicit account of how much to trust
the result.

---

## Project status

**The foundation is complete and runs. The detection pipeline is not implemented
yet.**

| Area | State |
| --- | --- |
| Backend service, config, typed errors, capability reporting | Working |
| `canopylens_ml` package, engine registry, dependency probing | Working |
| Frontend shell: upload, settings, map/imagery preview, results layout, limitations | Working |
| Docker images, compose stack, dev scripts, tests | Working |
| Detection engines, tiling, georeferencing, boundary clipping, export | **Not built yet** |

Because no engine is registered yet, **Run analysis is disabled** and says why.
No part of the UI shows an invented tree count, area, or coverage figure to fill
that gap — metrics read `—` until a real analysis produces them.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#not-built-yet) for the full list
of what remains.

---

## What it will do

- Detect individual tree crowns in uploaded imagery and count them
- Draw each crown as a clickable polygon over the imagery
- Report per-crown area, total canopy area, analysed area, and canopy coverage %
- Report a confidence value per crown and a confidence distribution overall
- Clip detections to an uploaded KML/KMZ boundary
- Export results as CSV and GeoJSON
- Say clearly when the imagery cannot support a physical-area estimate

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
- **No ground-truth validation is performed.** Nothing this tool outputs is a
  field-verified tree inventory, and it should not be presented as one.

Where a number cannot be computed honestly it is not computed. Imagery without
sufficient geospatial metadata yields pixel-based figures explicitly labelled as
such rather than a square-metre value that looks authoritative and is not.

---

## Roadmap

1. **Detection engines** — classical CV baseline (vegetation index + watershed)
   registered first so the app always has a working path; DeepForest added as
   the higher-quality optional engine.
2. **Raster pipeline** — windowed reading, overlapping tiles, NMS deduplication
   at tile boundaries.
3. **Geospatial layer** — CRS preservation, KML/KMZ parsing and reprojection
   into the raster CRS, boundary clipping, area via a projected CRS.
4. **Results and interaction** — crown polygons on the map, click-to-inspect
   (Crown ID, confidence, area, centroid), the full metrics panel.
5. **Export** — CSV and GeoJSON downloads generated from the real analysis.
6. **Deployment** — frontend on Vercel, backend on Render or Fly.

---

## License

MIT — see [LICENSE](LICENSE).
