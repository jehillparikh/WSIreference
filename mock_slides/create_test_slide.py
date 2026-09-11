"""
mock_slides/create_test_slide.py
Generate a small pyramidal TIFF for local WSI viewer testing.

Requirements
────────────
    pip install tifffile numpy

The output is a tiled, multi-resolution TIFF (4096×3072 px base) that
OpenSeadragon's GeoTIFFTileSource can stream tile-by-tile via Range requests.
The image is a colorful gradient + white grid — easy to confirm that zoom
and pan are working.

Usage
─────
    python mock_slides/create_test_slide.py
    # → mock_slides/sample.tiff  (~3-5 MB)
"""
from __future__ import annotations

import sys
from pathlib import Path


def _ensure_deps() -> None:
    """Install tifffile and numpy if not present (dev convenience only)."""
    missing = []
    for pkg in ("tifffile", "numpy"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        import subprocess
        print(f"Installing missing packages: {missing}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])


def create_test_slide(output_path: str, width: int = 4096, height: int = 3072) -> None:
    """
    Write a pyramidal TIFF to *output_path*.

    Pyramid levels
    ──────────────
      Level 0 (base)  :  4096 × 3072
      Level 1         :  2048 × 1536
      Level 2         :  1024 × 768
      Level 3         :   512 × 384

    Each level is tiled at 256×256 and JPEG-compressed.
    Sub-IFDs are marked with SUBFILETYPE=1 (RRIMAGE) so geotiff.js can
    discover them as pyramid levels.
    """
    import numpy as np
    import tifffile

    print(f"Generating {width}x{height} test slide -> {output_path}")

    # ── Build base image ────────────────────────────────────────────────────
    # Gradient: red increases left→right, green increases top→bottom
    col = (np.linspace(20, 240, width, dtype=np.float32)).astype(np.uint8)
    row = (np.linspace(20, 240, height, dtype=np.float32)).astype(np.uint8)

    r = np.tile(col,           (height, 1))           # (H, W)
    g = np.tile(row[:, None],  (1, width))             # (H, W)
    b = np.full((height, width), 140, dtype=np.uint8)

    # White grid lines every 512 px — visually verifiable at any zoom level
    for gx in range(0, width, 512):
        r[:, gx] = g[:, gx] = b[:, gx] = 255
    for gy in range(0, height, 512):
        r[gy, :] = g[gy, :] = b[gy, :] = 255

    base = np.stack([r, g, b], axis=-1)    # shape (H, W, 3), dtype uint8

    # ── Build pyramid (nearest-neighbour 2× downsampling) ──────────────────
    levels = [base]
    current = base
    for _ in range(3):
        current = current[::2, ::2]
        levels.append(current)

    # ── Write multi-IFD tiled TIFF ──────────────────────────────────────────
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with tifffile.TiffWriter(str(out), bigtiff=False) as tif:
        write_opts: dict = dict(
            tile=(256, 256),
            photometric="rgb",
            compression="deflate",   # zlib — built into tifffile, no imagecodecs needed
        )
        # subfiletype=0 → full-resolution primary image
        tif.write(levels[0], subfiletype=0, **write_opts)
        # subfiletype=1 → RRIMAGE (reduced-resolution) pyramid levels
        for level in levels[1:]:
            tif.write(level, subfiletype=1, **write_opts)

    size_kb = out.stat().st_size // 1024
    print(f"Done ({size_kb} KB) -> {out.resolve()}")
    print()
    print("Next steps:")
    print("  1. Start the backend:  python main.py")
    print("  2. Start the frontend: cd frontend && npm run dev")
    print("  3. Open http://localhost:5173/login")
    print("     Patient ID / Event ID / Slide ID can be anything (e.g. P1 / E1 / sample)")


if __name__ == "__main__":
    _ensure_deps()

    script_dir = Path(__file__).parent
    output = script_dir / "sample.tiff"

    if output.exists():
        answer = input(f"{output} already exists. Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("Skipped.")
            sys.exit(0)

    create_test_slide(str(output))
