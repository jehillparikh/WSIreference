/**
 * src/pages/SessionGate.jsx
 * Login form to capture patient_id / event_id / selected_slide_id.
 * On submit: stores session → navigates to viewer.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSession } from '../hooks/useSession'
import styles from './SessionGate.module.css'

export default function SessionGate() {
  const { setSession } = useSession()
  const navigate = useNavigate()
  const [form, setForm] = useState({ patientId: '', eventId: '', selectedSlideId: '' })
  const [err, setErr] = useState('')

  const onChange = (e) => setForm(f => ({ ...f, [e.target.name]: e.target.value }))

  const onSubmit = (e) => {
    e.preventDefault()
    const { patientId, eventId, selectedSlideId } = form
    if (!patientId.trim() || !eventId.trim() || !selectedSlideId.trim()) {
      setErr('All fields are required.')
      return
    }
    setSession({ patientId: patientId.trim(), eventId: eventId.trim(), selectedSlideId: selectedSlideId.trim() })
    navigate('/')
  }

  return (
    <div className={styles.overlay}>
      {/* Background orbs */}
      <div className={styles.orb1} aria-hidden />
      <div className={styles.orb2} aria-hidden />

      <div className={`${styles.card} glass`} role="main">
        {/* Logo */}
        <div className={styles.logo}>
          <svg width="44" height="44" viewBox="0 0 44 44" fill="none" aria-hidden>
            <rect width="44" height="44" rx="12" fill="url(#lg)"/>
            <circle cx="22" cy="22" r="10" stroke="white" strokeWidth="2.5" opacity=".9"/>
            <circle cx="22" cy="22" r="5"  fill="white" opacity=".9"/>
            <defs>
              <linearGradient id="lg" x1="0" y1="0" x2="44" y2="44" gradientUnits="userSpaceOnUse">
                <stop stopColor="#14b8a6"/>
                <stop offset="1" stopColor="#6366f1"/>
              </linearGradient>
            </defs>
          </svg>
          <span className={styles.logoText}>WSI Viewer</span>
        </div>

        <h1 className={styles.title}>Open a Case</h1>
        <p className={styles.subtitle}>Enter the session identifiers to load a slide.</p>

        <form onSubmit={onSubmit} className={styles.form} noValidate>
          <Field label="Patient ID"        name="patientId"       value={form.patientId}       onChange={onChange} placeholder="e.g. PAT-00123" />
          <Field label="Event ID"          name="eventId"         value={form.eventId}         onChange={onChange} placeholder="e.g. EVT-20240601" />
          <Field label="Selected Slide ID" name="selectedSlideId" value={form.selectedSlideId} onChange={onChange} placeholder="e.g. B1" />

          {err && <p className={styles.error} role="alert">{err}</p>}

          <button type="submit" className={styles.submit}>
            Open Viewer →
          </button>
        </form>
      </div>
    </div>
  )
}

function Field({ label, name, value, onChange, placeholder }) {
  return (
    <div className={styles.field}>
      <label htmlFor={name}>{label}</label>
      <input
        id={name} name={name} type="text"
        value={value} onChange={onChange}
        placeholder={placeholder} autoComplete="off"
        required
      />
    </div>
  )
}
