/**
 * src/hooks/useSession.js
 * Zustand store for the active WSI session (patient/event/slide IDs).
 * Persists to sessionStorage so page refresh doesn't lose context.
 */
import { create } from 'zustand'

const SESSION_KEY = 'wsi_session'

function loadStored() {
  try { return JSON.parse(sessionStorage.getItem(SESSION_KEY)) } catch (_) { return null }
}

export const useSession = create((set, get) => ({
  session: loadStored(),   // { patientId, eventId, selectedSlideId } | null

  setSession: (s) => {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(s))
    set({ session: s })
  },

  clearSession: () => {
    sessionStorage.removeItem(SESSION_KEY)
    set({ session: null })
  },

  isOpen: () => get().session !== null,
}))

/* ── Derive slide key (filename stem) from a slide object ── */
export function slideKey(slide) {
  if (!slide) return null
  const name = slide.filename || slide.slide_id || ''
  return name.replace(/\.[^/.]+$/, '')   // strip extension
}
