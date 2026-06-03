/**
 * src/api/client.js
 * Typed fetch wrappers for the WSI Viewer backend.
 * Used by React Query hooks in src/hooks/.
 */

const BASE = ''   // same-origin in prod; Vite proxies /api/* to :8080 in dev

function sessionQs(session) {
  if (!session) return ''
  const p = new URLSearchParams({
    patient_id: session.patientId,
    event_id:   session.eventId,
    selected_slide_id: session.selectedSlideId,
  })
  return '?' + p.toString()
}

async function request(url, opts = {}) {
  const res = await fetch(BASE + url, opts)
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch (_) {}
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  if (res.status === 204) return null
  return res.json()
}

/* ── Slides ──────────────────────────────────────────────────────────────── */
export const getSlides      = (session) => request(`/api/slides${sessionQs(session)}`)
export const thumbnailUrl   = (slideId, session) =>
  `${BASE}/api/thumbnail/${encodeURIComponent(slideId)}${sessionQs(session)}`
export const rawSlideUrl    = (filename, session) =>
  `${BASE}/api/raw_slides/${encodeURIComponent(filename)}${sessionQs(session)}`
export const getOverlayConfig = (slideName, session) =>
  request(`/api/overlay-config/${encodeURIComponent(slideName)}${sessionQs(session)}`)

/* ── TCA ─────────────────────────────────────────────────────────────────── */
export const getTCAAnalysis = (slideKey, session) =>
  request(`/api/tca/${encodeURIComponent(slideKey)}${sessionQs(session)}`)
export const getTCASummary  = (slideKey, session) =>
  request(`/api/tca/${encodeURIComponent(slideKey)}/summary${sessionQs(session)}`)
export const getTCAGrid     = (slideKey, session) =>
  request(`/api/tca/${encodeURIComponent(slideKey)}/grid${sessionQs(session)}`)
export const invalidateTCA  = (slideKey, session) =>
  request(`/api/tca/${encodeURIComponent(slideKey)}/cache${sessionQs(session)}`,
    { method: 'DELETE' })

/* ── Annotations ─────────────────────────────────────────────────────────── */
export const getAnnotations   = (key) => request(`/api/annotations/${encodeURIComponent(key)}`)
export const clearAnnotations = (key) =>
  request(`/api/annotations/${encodeURIComponent(key)}`, { method: 'DELETE' })

const ann = (key, type, body, method = 'POST', id = '') =>
  request(`/api/annotations/${encodeURIComponent(key)}/${type}${id ? '/'+id : ''}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })

export const addPolygon    = (key, v, label) => ann(key, 'polygons', { vertices: v, label })
export const deletePolygon = (key, id)        => ann(key, 'polygons', null, 'DELETE', id)
export const addLabel      = (key, anchor, offset, text) => ann(key, 'labels', { anchor, offset, text })
export const deleteLabel   = (key, id)        => ann(key, 'labels', null, 'DELETE', id)
export const addMeasure    = (key, start, end, w, h, mpp) =>
  ann(key, 'measures', { start, end, slide_width_px: w, slide_height_px: h, mpp })
export const deleteMeasure = (key, id)        => ann(key, 'measures', null, 'DELETE', id)

/* ── Worklist ────────────────────────────────────────────────────────────── */
export const listWorklist   = (params = {}) => {
  const p = new URLSearchParams()
  if (params.status)     p.set('status',      params.status)
  if (params.assignedTo) p.set('assigned_to', params.assignedTo)
  if (params.priority)   p.set('priority',    params.priority)
  p.set('limit',  params.limit  ?? 50)
  p.set('offset', params.offset ?? 0)
  return request(`/api/worklist?${p}`)
}
export const getWorklistStats  = () => request('/api/worklist/stats')
export const getWorklistItem   = (id) => request(`/api/worklist/${id}`)
export const addWorklistItem   = (body) =>
  request('/api/worklist', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
      patient_id: body.patientId, event_id: body.eventId,
      selected_slide_id: body.selectedSlideId, priority: body.priority ?? 3,
      assigned_to: body.assignedTo || null, notes: body.notes || null,
    }) })
export const updateWorklistItem = (id, patch) =>
  request(`/api/worklist/${id}`, { method: 'PATCH',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) })
export const deleteWorklistItem = (id) =>
  request(`/api/worklist/${id}`, { method: 'DELETE' })
