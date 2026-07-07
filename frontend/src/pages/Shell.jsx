/**
 * src/pages/Shell.jsx
 * App shell: collapsible sidebar nav + topbar → Viewer / TCA / Worklist panels.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Microscope, BarChart2, ClipboardList, LogOut, ChevronLeft, ChevronRight } from 'lucide-react'
import { useSession } from '../hooks/useSession'
import ViewerPanel   from './ViewerPanel'
import TCAPanel      from './TCAPanel'
import WorklistPanel from './WorklistPanel'
import Toast         from '../components/Toast'
import styles from './Shell.module.css'

const NAV_ITEMS = [
  { id: 'viewer',   label: 'Slide Viewer', title: 'Slide Viewer',            Icon: Microscope    },
  { id: 'tca',      label: 'TCA',          title: 'Tumour Content Analysis', Icon: BarChart2     },
  { id: 'worklist', label: 'Worklist',     title: 'Review Worklist',         Icon: ClipboardList },
]

export default function Shell() {
  const { session, clearSession } = useSession()
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState('viewer')
  const [activeSlide, setActiveSlide] = useState(null)   // shared across panels
  const [collapsed, setCollapsed] = useState(false)

  const handleLogout = () => {
    clearSession()
    navigate('/login')
  }

  const active = NAV_ITEMS.find(n => n.id === activeTab)

  return (
    <div className={styles.shell}>
      {/* ── Sidebar ─────────────────────────────────────────────────── */}
      <aside className={`${styles.sidebar} ${collapsed ? styles.sidebarCollapsed : ''}`}>
        <div className={styles.logoHeader}>
          <svg width="26" height="26" viewBox="0 0 44 44" fill="none" aria-hidden style={{ flexShrink: 0 }}>
            <rect width="44" height="44" rx="12" fill="var(--c-accent)" />
            <circle cx="22" cy="22" r="10" stroke="white" strokeWidth="2.5" opacity=".9" />
            <circle cx="22" cy="22" r="5"  fill="white" opacity=".9" />
          </svg>
          {!collapsed && (
            <span className={styles.wordmark}>WSI<em>Viewer</em></span>
          )}
        </div>
        <div className="glow-line" />

        <div className={styles.navSection}>
          {!collapsed && <span className={styles.navLabel}>Modules</span>}
          <nav className={styles.nav}>
            {NAV_ITEMS.map(({ id, label, Icon }) => (
              <button
                key={id}
                className={`${styles.navBtn} ${activeTab === id ? styles.navBtnActive : ''}`}
                onClick={() => setActiveTab(id)}
                title={collapsed ? label : undefined}
              >
                <Icon size={16} strokeWidth={2} />
                {!collapsed && <span>{label}</span>}
              </button>
            ))}
          </nav>
        </div>

        <button className={styles.collapseBtn} onClick={() => setCollapsed(c => !c)}
                title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
          {collapsed ? <ChevronRight size={15} /> : <ChevronLeft size={15} />}
        </button>
      </aside>

      {/* ── Main column ─────────────────────────────────────────────── */}
      <div className={styles.main}>
        <header className={`${styles.topbar} glass`}>
          <div className={styles.topbarLeft}>
            <h1 className={styles.pageTitle}>{active?.title}</h1>
            <span className={styles.pageSubtitle}>Pathology Intelligence Platform</span>
          </div>
          <div className={styles.topbarRight}>
            {session && (
              <span className={styles.sessionBadge}>
                {session.patientId} · {session.eventId} · {session.selectedSlideId}
              </span>
            )}
            <button className={styles.logoutBtn} onClick={handleLogout} title="End session">
              <LogOut size={15} strokeWidth={2} />
              New Case
            </button>
          </div>
        </header>

        <div className={styles.content}>
          {activeTab === 'viewer'   && <ViewerPanel   session={session} onSlideChange={setActiveSlide} />}
          {activeTab === 'tca'      && <TCAPanel      session={session} activeSlide={activeSlide} />}
          {activeTab === 'worklist' && <WorklistPanel session={session} />}
        </div>
      </div>

      <Toast />
    </div>
  )
}
