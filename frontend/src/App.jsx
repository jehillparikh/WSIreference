/**
 * src/App.jsx
 * Root component: session gate → main shell with tab routing.
 */
import { useEffect } from 'react'
import { Routes, Route, Navigate, useSearchParams } from 'react-router-dom'
import { useSession } from './hooks/useSession'
import SessionGate from './pages/SessionGate'
import Shell       from './pages/Shell'

export default function App() {
  const { session, setSession } = useSession()
  const [searchParams] = useSearchParams()

  useEffect(() => {
    // If URL contains session parameters, auto-login
    const pId = searchParams.get('patient_id')
    const eId = searchParams.get('event_id')
    const sId = searchParams.get('selected_slide_id')
    
    if (pId && eId && sId) {
      setSession({ patientId: pId, eventId: eId, selectedSlideId: sId })
      // Optionally clean up the URL to just "/" without losing state:
      window.history.replaceState({}, '', '/')
    }
  }, [searchParams, setSession])

  return (
    <Routes>
      <Route path="/login" element={<SessionGate />} />
      <Route
        path="/*"
        element={session ? <Shell /> : <Navigate to="/login" replace />}
      />
    </Routes>
  )
}
