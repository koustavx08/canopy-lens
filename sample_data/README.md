# Sample data

This directory is intentionally empty in version control. Aerial and satellite
imagery files are large binaries, and the useful public datasets come with their
own licences, so CanopyLens links to them rather than vendoring them.

Everything below is free to download and needs no account unless noted.

## What the app needs

| Property | Requirement | Why |
| --- | --- | --- |
| Format | GeoTIFF (`.tif`/`.tiff`), PNG, or JPEG | GeoTIFF is the only format that carries georeferencing |
| Resolution | 0.5 m/pixel or finer, ideally 0.1–0.3 m | Below ~0.5 m/pixel individual crowns stop being separable |
| Bands | RGB (3-band, 8-bit) | The detection engines read visible-light imagery |
| Georeferencing | A CRS and an affine transform | Required for area in m²; without it the app reports pixel counts and says so |

A plain PNG or JPEG works for detection and counting, but **area in square
metres cannot be computed from it** — CanopyLens will tell you this explicitly
rather than printing a number it cannot justify.

## Recommended downloads

### 1. NEON Airborne Observation Platform — RGB camera mosaics (best first choice)

10 cm/pixel RGB GeoTIFFs over forested US sites, and the source imagery behind
the NeonTreeEvaluation benchmark and the DeepForest pretrained model — so it is
the closest thing to an in-domain test set for this app.

- Portal: <https://data.neonscience.org/data-products/DP3.30010.001>
- Pick a forested site (`BART`, `HARV`, `OSBS`, `TEAK`), any recent year, and
  download one 1 km × 1 km tile.
- Free account required for bulk download; single tiles are downloadable directly.

### 2. NeonTreeEvaluation benchmark (imagery + hand-annotated crowns)

Pre-cut evaluation crops with human-drawn crown boxes, useful for eyeballing how
close the detections are to expert annotation.

- <https://zenodo.org/records/5914554>
- <https://github.com/weecology/NeonTreeEvaluation>

### 3. USGS EarthExplorer / The National Map — NAIP

60 cm/pixel RGB+NIR aerial imagery covering the continental US.

- <https://apps.nationalmap.gov/downloader/> (choose "NAIP Plus")
- 60 cm is at the coarse end of what works: expect merged crowns in dense stands.

### 4. OpenAerialMap

Community-contributed drone and aerial imagery worldwide, often 5–15 cm/pixel.
Quality and georeferencing vary by contributor — check the metadata before
trusting the scale bar.

- <https://openaerialmap.org/>

### 5. Boundary files (KML/KMZ)

The boundary upload is optional; it clips detections to an area of interest.

- Draw one in Google Earth Web (<https://earth.google.com/web/>) and export as
  KML, or
- Draw a polygon at <https://geojson.io/> and save as GeoJSON.

The boundary is reprojected into the raster's CRS before clipping, so it does
not need to match the imagery's projection.

## Where to put files

Drop downloads straight into this directory:

```
sample_data/
  osbs_2021_rgb_tile.tif
  study_area.kml
```

`.gitignore` excludes everything here except this README, so large files stay
out of the repository.

## Making your own small test file

To crop a large download to something quick to iterate on (requires the backend
environment, which already includes GDAL through rasterio):

```bash
# 2000 x 2000 pixel window starting at pixel (5000, 5000)
gdal_translate -srcwin 5000 5000 2000 2000 big_tile.tif sample_data/small_test.tif
```

To verify a file has usable georeferencing before uploading it:

```bash
python -c "import rasterio,sys; r=rasterio.open(sys.argv[1]); print(r.crs, r.res, r.count, r.dtypes)" sample_data/small_test.tif
```

A printed CRS and a resolution in metres mean area estimation will work. `None`
for the CRS means the file is a plain image as far as CanopyLens is concerned.
