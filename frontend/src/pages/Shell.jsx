/**
 * src/pages/Shell.jsx
 * Main application shell: top bar + tab navigation → Viewer / TCA / Worklist panels.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Microscope, BarChart2, ClipboardList, LogOut } from 'lucide-react'
import { useSession } from '../hooks/useSession'
import ViewerPanel   from './ViewerPanel'
import TCAPanel      from './TCAPanel'
import WorklistPanel from './WorklistPanel'
import Toast         from '../components/Toast'
import styles from './Shell.module.css'

const TABS = [
  { id: 'viewer',   label: 'Viewer',    Icon: Microscope   },
  { id: 'tca',      label: 'TCA',       Icon: BarChart2    },
  { id: 'worklist', label: 'Worklist',  Icon: ClipboardList },
]

export default function Shell() {
  const { session, clearSession } = useSession()
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState('viewer')
  const [activeSlide, setActiveSlide] = useState(null)   // shared across panels

  const handleLogout = () => {
    clearSession()
    navigate('/login')
  }

  return (
    <div className={styles.shell}>
      {/* ── Top bar ─────────────────────────────────────────────────── */}
      <header className={`${styles.topbar} glass`}>
        <div className={styles.topbarLeft}>
          <div className={styles.brand}>
            <svg width="28" height="28" viewBox="0 0 44 44" fill="none" aria-hidden>
              <rect width="44" height="44" rx="12" fill="url(#lg2)"/>
              <circle cx="22" cy="22" r="10" stroke="white" strokeWidth="2.5" opacity=".9"/>
              <circle cx="22" cy="22" r="5"  fill="white" opacity=".9"/>
              <defs>
                <linearGradient id="lg2" x1="0" y1="0" x2="44" y2="44" gradientUnits="userSpaceOnUse">
                  <stop stopColor="#14b8a6"/><stop offset="1" stopColor="#6366f1"/>
                </linearGradient>
              </defs>
            </svg>
            <span className={styles.brandName}>WSI Viewer</span>
          </div>
          {session && (
            <span className={styles.sessionBadge}>
              {session.patientId} · {session.eventId} · {session.selectedSlideId}
            </span>
          )}
        </div>

        <nav className={styles.tabs} role="tablist">
          {TABS.map(({ id, label, Icon }) => (
            <button
              key={id}
              role="tab"
              aria-selected={activeTab === id}
              className={`${styles.tab} ${activeTab === id ? styles.tabActive : ''}`}
              onClick={() => setActiveTab(id)}
            >
              <Icon size={15} strokeWidth={2} />
              {label}
            </button>
          ))}
        </nav>

        <div className={styles.topbarRight}>
          <button className={styles.logoutBtn} onClick={handleLogout} title="End session">
            <LogOut size={15} strokeWidth={2} />
            New Case
          </button>
        </div>
      </header>

      {/* ── Panels ──────────────────────────────────────────────────── */}
      <div className={styles.content}>
        {activeTab === 'viewer'   && <ViewerPanel   session={session} onSlideChange={setActiveSlide} />}
        {activeTab === 'tca'      && <TCAPanel      session={session} activeSlide={activeSlide} />}
        {activeTab === 'worklist' && <WorklistPanel session={session} />}
      </div>

      <Toast />
    </div>
  )
}
