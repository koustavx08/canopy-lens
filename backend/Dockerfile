# CanopyLens API
# ---------------------------------------------------------------------------
# Build context is the REPOSITORY ROOT, not backend/, because the image needs
# both `backend/` (the service) and `ml/` (the detection package installed in
# editable-equivalent form):
#
#   docker build -f backend/Dockerfile -t canopylens-api .
#
# The optional deep-learning engine (requirements-ml.txt) is deliberately NOT
# installed here: torch plus pretrained weights add roughly 2.5 GB, which is
# over the image-size budget of most free container hosts. Without it the API
# still runs and /api/capabilities honestly reports which engines are usable.
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# opencv-python-headless and rasterio ship manylinux wheels that bundle their
# native stacks (GDAL, PROJ, GEOS), so only glib and expat are needed at runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 libexpat1 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so code edits do not invalidate the wheel-install layer.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY ml/ ml/
RUN pip install --no-cache-dir ./ml

COPY backend/ backend/

# Non-root, with ownership of the writable data directory.
RUN useradd --create-home --uid 10001 canopy \
 && mkdir -p /app/backend/.data \
 && chown -R canopy:canopy /app/backend/.data
USER canopy

ENV CANOPYLENS_HOST=0.0.0.0 \
    CANOPYLENS_PORT=8000 \
    CANOPYLENS_DATA_DIR=/app/backend/.data \
    PYTHONPATH=/app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# Hosts such as Render and Railway inject $PORT; fall back to 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --app-dir /app/backend --host 0.0.0.0 --port ${PORT:-8000}"]
