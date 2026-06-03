/**
 * src/pages/WorklistPanel.jsx
 * Pathology review worklist: table, filters, add-case modal, pagination.
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, RefreshCw, X } from 'lucide-react'
import { listWorklist, getWorklistStats, addWorklistItem,
         updateWorklistItem, deleteWorklistItem } from '../api/client'
import { useToast } from '../components/Toast'
import styles from './WorklistPanel.module.css'

const STATUS_COLOURS = {
  PENDING:   '#f59e0b', IN_REVIEW: '#6366f1',
  COMPLETE:  '#22c55e', FLAGGED:   '#ef4444', CANCELLED: '#64748b',
}
const PRIORITY_LABELS = { 1:'Urgent', 2:'High', 3:'Normal', 4:'Low', 5:'Routine' }
const LIMIT = 25

export default function WorklistPanel({ session }) {
  const toast = useToast()
  const qc    = useQueryClient()

  const [filters, setFilters] = useState({ status: '', assignedTo: '', priority: '' })
  const [offset,  setOffset]  = useState(0)
  const [showAdd, setShowAdd] = useState(false)

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['worklist'] })
    qc.invalidateQueries({ queryKey: ['worklist-stats'] })
  }

  const { data, isLoading } = useQuery({
    queryKey: ['worklist', filters, offset],
    queryFn: () => listWorklist({
      status:     filters.status     || undefined,
      assignedTo: filters.assignedTo || undefined,
      priority:   filters.priority ? parseInt(filters.priority) : undefined,
      limit: LIMIT, offset,
    }),
  })

  const { data: statsData } = useQuery({
    queryKey: ['worklist-stats'],
    queryFn:  getWorklistStats,
  })

  const { mutate: changeStatus } = useMutation({
    mutationFn: ({ id, status }) => updateWorklistItem(id, { status }),
    onSuccess: () => { invalidate(); toast.show('Status updated', 'success') },
    onError: (e) => toast.show(e.message, 'error'),
  })

  const { mutate: remove } = useMutation({
    mutationFn: (id) => deleteWorklistItem(id),
    onSuccess: () => { invalidate(); toast.show('Item removed', 'info') },
    onError: (e) => toast.show(e.message, 'error'),
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const pages = Math.ceil(total / LIMIT)
  const curPage = Math.floor(offset / LIMIT)

  const onFilterChange = (key, val) => {
    setFilters(f => ({ ...f, [key]: val }))
    setOffset(0)
  }

  const openInViewer = (item) => {
    const url = new URL(window.location.href)
    url.searchParams.set('patient_id', item.patient_id)
    url.searchParams.set('event_id', item.event_id)
    url.searchParams.set('selected_slide_id', item.selected_slide_id)
    window.location.href = url.toString()
  }

  return (
    <div className={styles.layout}>
      {/* Header */}
      <div className={styles.header}>
        <h2 className={styles.title}>Review Worklist</h2>
        <div className={styles.headerActions}>
          <button className={styles.btnAdd} onClick={() => setShowAdd(true)}>
            <Plus size={14} strokeWidth={2.5} /> Add Case
          </button>
          <button className={styles.btnIcon} onClick={invalidate} title="Refresh">
            <RefreshCw size={14} strokeWidth={2} />
          </button>
        </div>
      </div>

      {/* Stats chips */}
      {statsData && (
        <div className={`${styles.statsBar} glass`}>
          {Object.entries(statsData.counts).map(([status, count]) => (
            <span key={status} className={styles.chip}>
              <span className={styles.chipDot} style={{ background: STATUS_COLOURS[status] || '#64748b' }} />
              {status.replace('_', ' ')}: <strong>{count}</strong>
            </span>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className={`${styles.filters} glass`}>
        <select value={filters.status} onChange={e => onFilterChange('status', e.target.value)}
                aria-label="Filter by status">
          <option value="">All Statuses</option>
          {Object.keys(STATUS_COLOURS).map(s => <option key={s} value={s}>{s.replace('_',' ')}</option>)}
        </select>
        <input
          type="text" value={filters.assignedTo} placeholder="Assignee…"
          onChange={e => onFilterChange('assignedTo', e.target.value)}
          aria-label="Filter by assignee"
        />
        <select value={filters.priority} onChange={e => onFilterChange('priority', e.target.value)}
                aria-label="Filter by priority">
          <option value="">All Priorities</option>
          {[1,2,3,4,5].map(p => <option key={p} value={p}>{p} — {PRIORITY_LABELS[p]}</option>)}
        </select>
      </div>

      {/* Table */}
      <div className={`${styles.tableWrap} glass`}>
        {isLoading ? (
          <div className={styles.empty}>Loading…</div>
        ) : items.length === 0 ? (
          <div className={styles.empty}>No items match your filters.</div>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Pri</th><th>Patient</th><th>Event</th><th>Slide</th>
                <th>Status</th><th>Assigned To</th><th>Created</th><th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map(item => (
                <tr key={item.id}>
                  <td><PriBadge p={item.priority} /></td>
                  <td>{item.patient_id}</td>
                  <td>{item.event_id}</td>
                  <td>{item.selected_slide_id}</td>
                  <td><StatusPill status={item.status} /></td>
                  <td className={styles.dimCell}>{item.assigned_to || '—'}</td>
                  <td className={styles.dimCell}>{new Date(item.created_at).toLocaleDateString()}</td>
                  <td>
                    <div className={styles.actions}>
                      <button className={`${styles.actionBtn} ${styles.openBtn}`}
                        onClick={() => openInViewer(item)} title="Open in viewer">▶</button>
                      <StatusSelect item={item} onChange={(id, s) => changeStatus({ id, status: s })} />
                      <button className={`${styles.actionBtn} ${styles.delBtn}`}
                        onClick={() => confirm('Remove this item?') && remove(item.id)} title="Delete">✕</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {pages > 1 && (
        <div className={styles.pagination}>
          {Array.from({ length: pages }, (_, i) => (
            <button key={i}
              className={`${styles.pageBtn} ${i === curPage ? styles.pageBtnActive : ''}`}
              onClick={() => setOffset(i * LIMIT)}>
              {i + 1}
            </button>
          ))}
        </div>
      )}

      {/* Add case modal */}
      {showAdd && <AddCaseModal onClose={() => setShowAdd(false)} onAdded={invalidate} toast={toast} />}
    </div>
  )
}

/* ── Sub-components ─────────────────────────────────────────────────────── */

function PriBadge({ p }) {
  const cls = [styles.priBase, styles[`pri${p}`]].join(' ')
  return <span className={cls}>{p}</span>
}

function StatusPill({ status }) {
  return (
    <span className={`${styles.pill} ${styles[`pill${status.replace('_','')}`]}`}>
      {status.replace('_', ' ')}
    </span>
  )
}

function StatusSelect({ item, onChange }) {
  return (
    <select className={styles.statusSelect} value={item.status}
            onChange={e => onChange(item.id, e.target.value)}
            aria-label="Change status">
      {['PENDING','IN_REVIEW','COMPLETE','FLAGGED','CANCELLED'].map(s =>
        <option key={s} value={s}>{s.replace('_',' ')}</option>
      )}
    </select>
  )
}

function AddCaseModal({ onClose, onAdded, toast }) {
  const [form, setForm] = useState({ patientId:'', eventId:'', selectedSlideId:'', priority:3, assignedTo:'', notes:'' })
  const { mutate, isPending } = useMutation({
    mutationFn: () => addWorklistItem(form),
    onSuccess: () => { toast.show('Case added', 'success'); onAdded(); onClose() },
    onError: (e) => toast.show(e.message, 'error'),
  })
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  return (
    <div className={styles.modalOverlay} onClick={e => e.target === e.currentTarget && onClose()}>
      <div className={`${styles.modal} glass`} role="dialog" aria-modal aria-labelledby="modal-title">
        <div className={styles.modalHeader}>
          <h3 id="modal-title">Add Case to Worklist</h3>
          <button className={styles.closeBtn} onClick={onClose}><X size={16} /></button>
        </div>
        <div className={styles.modalForm}>
          <MF label="Patient ID"   value={form.patientId}       onChange={v => set('patientId', v)} />
          <MF label="Event ID"     value={form.eventId}         onChange={v => set('eventId', v)} />
          <MF label="Slide ID"     value={form.selectedSlideId} onChange={v => set('selectedSlideId', v)} />
          <div className={styles.modalRow}>
            <div className={styles.mf}>
              <label>Priority</label>
              <select value={form.priority} onChange={e => set('priority', parseInt(e.target.value))}>
                {[1,2,3,4,5].map(p => <option key={p} value={p}>{p} — {PRIORITY_LABELS[p]}</option>)}
              </select>
            </div>
            <MF label="Assign To" value={form.assignedTo} onChange={v => set('assignedTo', v)} placeholder="Optional" />
          </div>
          <div className={styles.mf}>
            <label>Notes</label>
            <textarea rows={3} value={form.notes} onChange={e => set('notes', e.target.value)} placeholder="Optional…" />
          </div>
        </div>
        <div className={styles.modalActions}>
          <button className={styles.btnCancel} onClick={onClose}>Cancel</button>
          <button className={styles.btnSubmit} onClick={() => mutate()} disabled={isPending}>
            {isPending ? 'Adding…' : 'Add to Worklist'}
          </button>
        </div>
      </div>
    </div>
  )
}

function MF({ label, value, onChange, placeholder }) {
  return (
    <div className={styles.mf}>
      <label>{label}</label>
      <input type="text" value={value} onChange={e => onChange(e.target.value)}
             placeholder={placeholder || ''} />
    </div>
  )
}
