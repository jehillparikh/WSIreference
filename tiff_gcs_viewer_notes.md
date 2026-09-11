# TIFF Loading Notes

The pathology viewer opens `.tif` and `.tiff` files through OpenSeadragon with GeoTIFF tile streaming. That means the image is fetched in small tiles, so it can appear to load square by square. This is expected for tiled, pyramidal TIFFs.

Slow loading usually means the TIFF is not tiled, not pyramidal, or is being served through the backend proxy instead of directly from GCS. Moving to GCS can help, but the biggest speedup comes from combining GCS direct access with tiled, pyramidal GeoTIFFs or similar WSI-friendly formats.

If you want the best performance, use a cloud-optimized, tiled pyramid rather than a flat TIFF.