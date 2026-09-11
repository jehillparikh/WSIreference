# mock_slides/

Local slide files for development / testing without a real GCS bucket or
pathology API.

## Quick start

```powershell
# 1. Install generator dependencies (one-time)
pip install tifffile numpy

# 2. Generate a small synthetic test slide
python mock_slides/create_test_slide.py
# → mock_slides/sample.tiff  (~3–5 MB)

# 3. Activate mock mode in .env  (already set by default)
#    EXTERNAL_API_BASE_URL=mock
#    MOCK_SLIDE_DIR=mock_slides

# 4. Start the backend
python main.py

# 5. Start the frontend dev server (separate terminal)
cd frontend
npm run dev

# 6. Open http://localhost:5173/login
#    Patient ID / Event ID / Slide ID can be anything — e.g. P1 / E1 / sample
```

---

## Using your own slides

Drop any `.tiff`, `.svs`, `.ndpi`, `.tif` file into this directory.
The mock API scans the directory on every session request, so no restart
is needed after adding files.

**Supported formats** (anything GeoTIFF.js can read via Range requests):
- Cloud-Optimized GeoTIFF (`.tiff` / `.tif`) — best performance
- Aperio SVS (`.svs`) — tiled TIFF variant, works directly
- Hamamatsu NDPI (`.ndpi`) — partial support via geotiff.js

> **Tip:** For best tile-streaming performance, use a Cloud-Optimized GeoTIFF.
> Convert any image with [sharp](https://sharp.pixelplumbing.com/):
> ```js
> sharp('slide.svs').tiff({ tile: true, pyramid: true }).toFile('slide_cog.tiff')
> ```

---

## How mock mode works

```
Browser → FastAPI /api/slides
                ↓
        SlideSessionService._get_client()
                ↓  (EXTERNAL_API_BASE_URL=mock)
        MockPathologyAPIClient.fetch_slides()
                ↓  returns local:// URLs
        GCSStreamService.stream_range()
                ↓  local:// → reads from disk (Range support)
        FastAPI /api/raw_slides/{filename}
                ↓
        OpenSeadragon GeoTIFFTileSource (tile streaming)
```

No GCS credentials or real API keys are needed.
