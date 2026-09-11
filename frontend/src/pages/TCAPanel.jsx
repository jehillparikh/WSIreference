/**
 * src/pages/TCAPanel.jsx
 * TCA Analysis panel: stat cards, density bands, spatial heatmap.
 */
import { useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, AlertCircle } from 'lucide-react'
import { getTCAAnalysis, getTCAGrid, invalidateTCA } from '../api/client'
import { slideKey } from '../hooks/useSession'
import { useToast } from '../components/Toast'
import styles from './TCAPanel.module.css'

export default function TCAPanel({ session, activeSlide }) {
  const toast  = useToast()
  const qc     = useQueryClient()
  const key    = slideKey(activeSlide)

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['tca', key, session],
    queryFn:  () => getTCAAnalysis(key, session),
    enabled:  !!key,
  })

  const { data: gridData } = useQuery({
    queryKey: ['tca-grid', key, session],
    queryFn:  () => getTCAGrid(key, session),
    enabled:  !!key && !!data?.available,
  })

  const { mutate: refresh } = useMutation({
    mutationFn: () => invalidateTCA(key, session),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tca'] })
      qc.invalidateQueries({ queryKey: ['tca-grid'] })
      toast.show('TCA cache refreshed', 'info')
    },
    onError: (e) => toast.show(e.message, 'error'),
  })

  if (!activeSlide) return (
    <div className={styles.empty}>
      <AlertCircle size={40} opacity={.4} />
      <p>Open a slide in the Viewer tab first.</p>
    </div>
  )

  if (isLoading) return <div className={styles.empty}>Loading TCA analysis…</div>
  if (isError)   return <div className={styles.empty} style={{color:'var(--c-danger)'}}>
    {error.message}
  </div>

  const agg = data?.aggregates

  return (
    <div className={styles.layout}>
      <div className={styles.header}>
        <span />
        <button className={styles.refreshBtn} onClick={() => refresh()} title="Refresh TCA">
          <RefreshCw size={14} strokeWidth={2} /> Refresh
        </button>
      </div>

      {!data?.available ? (
        <div className={styles.empty}>
          <AlertCircle size={40} opacity={.4} />
          <p>No TCA data available for <strong>{key}</strong>.<br/>
          Open the slide in the viewer to trigger the TCA download.</p>
        </div>
      ) : (
        <>
          {/* Stat cards */}
          <div className={styles.statGrid}>
            <StatCard label="Tumour"  value={agg?.overall_tumour_pct}  colour="var(--c-tumour)"  />
            <StatCard label="Stroma"  value={agg?.overall_stroma_pct}  colour="var(--c-stroma)"  />
            <StatCard label="Necrosis"value={agg?.overall_necrosis_pct}colour="var(--c-necrosis)"/>
            <StatCard label="Hotspot" value={agg?.hotspot_tumour_pct}  colour="var(--c-danger)"
              sub={agg?.hotspot_row != null ? `Row ${agg.hotspot_row}, Col ${agg.hotspot_col}` : null} />
          </div>

          {/* Density bands */}
          {data.density_bands?.length > 0 && (
            <section className={`${styles.section} card`}>
              <h3 className={`${styles.sectionTitle} section-title`}>Density Bands</h3>
              <DensityBands bands={data.density_bands} />
            </section>
          )}

          {/* Spatial heatmap */}
          {gridData?.grid_cells?.length > 0 && (
            <section className={`${styles.section} card`}>
              <h3 className={`${styles.sectionTitle} section-title`}>
                Spatial Heatmap
                <span className={styles.sectionMeta}>
                  ({gridData.total} cells)
                </span>
              </h3>
              <Heatmap cells={gridData.grid_cells} />
            </section>
          )}

          {/* Analysed timestamp */}
          {data.analysed_at && (
            <p className={styles.ts}>Analysed: {new Date(data.analysed_at).toLocaleString()}</p>
          )}
        </>
      )}
    </div>
  )
}

/* ── Sub-components ───────────────────────────────────────────────────────── */

function StatCard({ label, value, colour, sub }) {
  const pct = value != null ? value : null
  return (
    <div className={`${styles.statCard} card`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{pct != null ? pct.toFixed(1) + '%' : '—'}</div>
      {pct != null && (
        <div className={styles.statBar}>
          <div className={styles.statBarFill} style={{ width: Math.min(pct, 100) + '%', background: colour }} />
        </div>
      )}
      {sub && <div className={styles.statSub}>{sub}</div>}
    </div>
  )
}

function DensityBands({ bands }) {
  const maxCount = Math.max(...bands.map(b => b.cell_count), 1)
  return (
    <div className={styles.bands}>
      {bands.map(b => (
        <div key={b.label} className={styles.bandRow}>
          <span className={styles.bandLabel}>{b.label}</span>
          <div className={styles.bandTrack}>
            <div className={styles.bandFill}
              style={{ width: (b.cell_count / maxCount * 100).toFixed(1) + '%' }} />
          </div>
          <span className={styles.bandCount}>{b.cell_count.toLocaleString()}</span>
        </div>
      ))}
    </div>
  )
}

function Heatmap({ cells }) {
  const canvasRef = useRef(null)
  useEffect(() => {
    if (!canvasRef.current || !cells.length) return
    const maxRow = Math.max(...cells.map(c => c.row)) + 1
    const maxCol = Math.max(...cells.map(c => c.col)) + 1
    const px = 8
    const canvas = canvasRef.current
    canvas.width  = maxCol * px
    canvas.height = maxRow * px
    const ctx = canvas.getContext('2d')
    cells.forEach(c => {
      ctx.fillStyle = tumourColor(c.tumour_pct / 100)
      ctx.fillRect(c.col * px, c.row * px, px, px)
    })
  }, [cells])
  return <canvas ref={canvasRef} className={styles.heatmap} />
}

function lerp(a, b, t) { return Math.round(a + (b - a) * t) }
function tumourColor(t) {
  if (t < 0.33) {
    const f = t / 0.33
    return `rgb(${lerp(30,20,f)},${lerp(100,180,f)},${lerp(180,166,f)})`
  } else if (t < 0.66) {
    const f = (t - 0.33) / 0.33
    return `rgb(${lerp(20,249,f)},${lerp(180,115,f)},${lerp(166,22,f)})`
  } else {
    const f = (t - 0.66) / 0.34
    return `rgb(${lerp(249,239,f)},${lerp(115,68,f)},${lerp(22,68,f)})`
  }
}
