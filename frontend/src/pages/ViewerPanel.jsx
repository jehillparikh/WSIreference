/**
 * src/pages/ViewerPanel.jsx
 * Viewer panel: slide sidebar + OpenSeadragon viewport + annotation toolbar.
 */
import { useEffect, useRef, useState, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Hand, Pentagon, Tag, Ruler, Layers, Trash2 } from 'lucide-react'
import { getSlides, rawSlideUrl, thumbnailUrl, getAnnotations, clearAnnotations,
         addPolygon, addLabel, addMeasure } from '../api/client'
import { slideKey } from '../hooks/useSession'
import { useToast } from '../components/Toast'
import styles from './ViewerPanel.module.css'

// Module-level flag: enableGeoTIFFTileSource must be called exactly once.
let _geoTiffEnabled = false

const TOOLS = [
  { id: 'pan',     label: 'Pan',     Icon: Hand },
  { id: 'polygon', label: 'Polygon', Icon: Pentagon   },
  { id: 'label',   label: 'Label',   Icon: Tag        },
  { id: 'measure', label: 'Measure', Icon: Ruler      },
]

export default function ViewerPanel({ session, onSlideChange }) {
  const toast = useToast()
  const osdRef    = useRef(null)    // OSD viewer instance
  const viewerEl  = useRef(null)    // DOM container ref
  const annCanvasRef = useRef(null)
  const overlayRef   = useRef(null)
  // Monotonic token: bumped on every openSlide() and on unmount so that a
  // slow, in-flight open (awaiting dynamic imports + Range requests) knows it
  // has been superseded and must not create a viewer. Prevents duplicate /
  // leaked OSD instances under React StrictMode's double-invoked effects.
  const openTokenRef = useRef(0)

  const [activeTool,   setActiveTool]   = useState('pan')
  const [activeSlide,  setActiveSlide]  = useState(null)
  const [overlayOn,    setOverlayOn]    = useState(false)
  const [zoom,         setZoom]         = useState(1)
  // Instant low-res overview: shown immediately on slide select, faded out
  // once OSD paints its first real tile (covers the GeoTIFF IFD-parsing
  // handshake, which otherwise leaves the viewport blank for a beat).
  const [overviewSrc,     setOverviewSrc]     = useState(null)
  const [overviewVisible, setOverviewVisible] = useState(false)
  const [drawPoints,   setDrawPoints]   = useState([])
  const [isDrawing,    setIsDrawing]    = useState(false)
  const [annotations,  setAnnotations]  = useState({ polygons: [], labels: [], measures: [] })

  /* ── Load slide list ──────────────────────────────────────────── */
  const { data: slideData, isLoading, error } = useQuery({
    queryKey: ['slides', session],
    queryFn:  () => getSlides(session),
    enabled:  !!session,
  })

  const slides = slideData?.slides ?? []

  /* ── Open OSD on first load ───────────────────────────────────── */
  useEffect(() => {
    if (!slideData || !viewerEl.current) return
    const defaultName = slideData.default_slide
    const defaultSlide = slides.find(s => s.filename === defaultName) || slides[0]
    if (defaultSlide) openSlide(defaultSlide)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slideData])

  /* ── OSD init & cleanup ───────────────────────────────────────────── */
  const openSlide = useCallback(async (slide) => {
    if (!viewerEl.current) return
    const myToken = ++openTokenRef.current   // this call now owns the viewport

    setActiveSlide(slide)
    onSlideChange?.(slide)

    // Show the pre-generated thumbnail immediately — the whole slide, at a
    // glance, before OSD/geotiff.js have even started parsing the TIFF.
    setOverviewSrc(thumbnailUrl(slide.filename || slide.slide_id, session))
    setOverviewVisible(true)

    const url = rawSlideUrl(slide.filename || slide.slide_id, session)
    console.log('[WSI Viewer] Opening slide URL:', url)

    // ── Load OSD + enable GeoTIFF tile source (once per module lifetime) ──
    const OSD = (await import('openseadragon')).default
    if (!_geoTiffEnabled) {
      const { enableGeoTIFFTileSource } = await import('geotiff-tilesource')
      enableGeoTIFFTileSource(OSD)
      _geoTiffEnabled = true
    }
    if (myToken !== openTokenRef.current) return   // superseded while importing

    // ── Build tile source — try GeoTIFF first, fall back to plain image ──
    let tileSource
    try {
      // getAllTileSources fetches the file via Range requests and returns an
      // ARRAY of GeoTIFFTileSource objects — one per image group in the file
      // (the full-resolution pyramid first, then any associated label / macro
      // / thumbnail images). We open ONLY the primary pyramid (index 0):
      // passing the whole array to OSD (with sequenceMode off) would add every
      // image as an overlapping TiledImage, stacking the tiny label/macro on
      // top of the slide — which is exactly the "broken viewer" symptom.
      const tileSources = await OSD.GeoTIFFTileSource.getAllTileSources(url, {
        logLevel: 1,    // show geotiff.js warnings in the browser console
        cache: false,   // always re-fetch when slide changes
      })
      if (myToken !== openTokenRef.current) return   // superseded while fetching
      if (!tileSources || !tileSources.length) {
        throw new Error('No renderable images found in TIFF')
      }
      tileSource = tileSources[0]
      console.log(
        `[WSI Viewer] GeoTIFFTileSource ready — ${tileSources.length} image group(s); ` +
        `opening primary with ${tileSource?.GeoTIFFImages?.length ?? '?'} pyramid level(s)`
      )
    } catch (err) {
      console.error('[WSI Viewer] GeoTIFFTileSource FAILED — check the Network tab for Range request errors:', err)
      // Browser cannot render TIFF natively; this fallback only works for JPEG/PNG thumbnails.
      tileSource = { type: 'image', url }
    }

    if (myToken !== openTokenRef.current) return
    // Tear down any previous viewer only now that we're ready to replace it.
    if (osdRef.current) { osdRef.current.destroy(); osdRef.current = null }

    const viewer = OSD({
      element: viewerEl.current,
      prefixUrl: 'https://cdn.jsdelivr.net/npm/openseadragon@6.0.2/build/openseadragon/images/',
      tileSources: tileSource,
      showNavigationControl: false,
      animationTime: 0.28,
      minZoomImageRatio: 0.4,
      maxZoomPixelRatio: 4,
    })
    osdRef.current = viewer
    viewer.addHandler('zoom', ({ zoom: z }) => setZoom(z))
    viewer.addHandler('open-failed', (e) => {
      console.error('[WSI Viewer] OSD open-failed:', e)
      toast.show('Failed to open slide — see console for details', 'error')
    })
    // First real tile loaded into memory — the low-res overview has done
    // its job, fade it out. NOTE: we listen for 'tile-loaded', not
    // 'tile-drawn' — OSD 6's default drawer is WebGL when available, and
    // WebGLDrawer explicitly never raises 'tile-drawn' (only the legacy
    // canvas/html drawers do), so that handler would silently never fire.
    // 'tile-loaded' fires at the image-loading layer, independent of which
    // drawer renders it.
    viewer.addOnceHandler('tile-loaded', () => {
      if (myToken === openTokenRef.current) setOverviewVisible(false)
    })

    // Load annotations
    const key = slideKey(slide)
    if (key) {
      try {
        const ann = await getAnnotations(key)
        if (myToken === openTokenRef.current) setAnnotations(ann)
      } catch (_) {
        if (myToken === openTokenRef.current) setAnnotations({ polygons: [], labels: [], measures: [] })
      }
    }
  }, [session, onSlideChange, toast])


  useEffect(() => () => {
    openTokenRef.current++          // invalidate any in-flight openSlide()
    osdRef.current?.destroy()
    osdRef.current = null
  }, [])

  /* ── Tool switch ──────────────────────────────────────────────── */
  const switchTool = (tool) => {
    setActiveTool(tool)
    setIsDrawing(false)
    setDrawPoints([])
    if (osdRef.current) osdRef.current.setMouseNavEnabled(tool === 'pan')
  }

  /* ── Clear all ────────────────────────────────────────────────── */
  const handleClear = async () => {
    const key = slideKey(activeSlide)
    if (!key || !confirm('Clear all annotations for this slide?')) return
    try {
      await clearAnnotations(key)
      setAnnotations({ polygons: [], labels: [], measures: [] })
      toast.show('Annotations cleared', 'info')
    } catch (e) { toast.show(e.message, 'error') }
  }

  /* ── Status label for active tool ────────────────────────────── */
  const toolLabel = TOOLS.find(t => t.id === activeTool)?.label ?? ''

  if (isLoading) return <div className={styles.center}>Loading slides…</div>
  if (error)     return <div className={styles.center} style={{color:'var(--c-danger)'}}>
    {error.message}
  </div>

  return (
    <div className={styles.panel}>
      {/* Sidebar */}
      <aside className={`${styles.sidebar} glass`}>
        <div className={styles.sidebarHeader}>
          <span className={styles.sidebarTitle}>Slides</span>
          <span className={styles.badge}>{slides.length}</span>
        </div>
        <ul className={styles.slideList} role="listbox">
          {slides.map(s => {
            const name = s.filename || s.slide_id
            const m    = s.meta_data
            const dim  = m ? `${Math.round(m.width/1000)}k × ${Math.round(m.height/1000)}k` : ''
            const isAct= activeSlide?.slide_id === s.slide_id
            return (
              <li key={s.slide_id}
                  role="option"
                  aria-selected={isAct}
                  className={`${styles.slideItem} ${isAct ? styles.slideItemActive : ''}`}
                  onClick={() => openSlide(s)}>
                <span className={styles.slideName}>{name}</span>
                <span className={styles.slideMeta}>
                  {dim && <span>{dim}</span>}
                  {s.has_tca && <span className={`${styles.pill} ${styles.pillTeal}`}>TCA</span>}
                  <span className={styles.pill}>{s.status}</span>
                </span>
              </li>
            )
          })}
        </ul>
      </aside>

      {/* Viewer main */}
      <div className={styles.viewerMain}>
        <div className={styles.viewportWrap}>
          <div ref={viewerEl} className={styles.viewport} id="osd-viewport" />
          {overviewSrc && (
            <img
              className={`${styles.overview} ${overviewVisible ? '' : styles.overviewHidden}`}
              src={overviewSrc}
              alt=""
              onError={() => setOverviewVisible(false)}
            />
          )}
          <canvas ref={annCanvasRef} className={styles.annCanvas} />
          <canvas ref={overlayRef}   className={styles.overlayCanvas} />
        </div>

        {/* Annotation toolbar */}
        <div className={`${styles.toolbar} glass`} role="toolbar">
          {TOOLS.map(({ id, label, Icon }) => (
            <button
              key={id}
              className={`${styles.toolBtn} ${activeTool === id ? styles.toolBtnActive : ''}`}
              title={label}
              onClick={() => switchTool(id)}
            >
              <Icon size={16} strokeWidth={2} />
            </button>
          ))}
          <span className={styles.tbDivider} />
          <button
            className={`${styles.toolBtn} ${overlayOn ? styles.toolBtnActive : ''}`}
            title="Toggle TCA Overlay"
            onClick={() => setOverlayOn(v => !v)}
          >
            <Layers size={16} strokeWidth={2} />
          </button>
          <span className={styles.tbDivider} />
          <button className={`${styles.toolBtn} ${styles.toolBtnDanger}`} title="Clear annotations" onClick={handleClear}>
            <Trash2 size={16} strokeWidth={2} />
          </button>
        </div>

        {/* Status bar */}
        <div className={`${styles.statusBar} glass`}>
          <span>Zoom: {zoom.toFixed(2)}×</span>
          <span className={styles.sep}>|</span>
          <span>{activeSlide?.filename || activeSlide?.slide_id || 'No slide'}</span>
          <span className={styles.sep}>|</span>
          <span>{toolLabel}</span>
          <span className={styles.sep}>|</span>
          <span>Polygons: {annotations.polygons.length}</span>
          <span className={styles.sep}>·</span>
          <span>Labels: {annotations.labels.length}</span>
          <span className={styles.sep}>·</span>
          <span>Measures: {annotations.measures.length}</span>
        </div>
      </div>
    </div>
  )
}
