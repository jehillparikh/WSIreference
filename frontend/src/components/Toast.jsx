/**
 * src/components/Toast.jsx
 * Global toast notification system using Zustand.
 *
 * Usage:
 *   const toast = useToast()
 *   toast.show('Saved!', 'success')   // 'success' | 'error' | 'info'
 *
 * Render <Toast /> once in Shell.jsx.
 */
import { create } from 'zustand'
import { useEffect } from 'react'
import { CheckCircle, XCircle, Info, X } from 'lucide-react'
import styles from './Toast.module.css'

let _nextId = 0

const useToastStore = create((set) => ({
  toasts: [],
  add:    (msg, type = 'info') => {
    const id = ++_nextId
    set(s => ({ toasts: [...s.toasts, { id, msg, type }] }))
    return id
  },
  remove: (id) => set(s => ({ toasts: s.toasts.filter(t => t.id !== id) })),
}))

export function useToast() {
  const { add } = useToastStore()
  return { show: add }
}

const ICONS = {
  success: <CheckCircle size={16} strokeWidth={2.5} />,
  error:   <XCircle    size={16} strokeWidth={2.5} />,
  info:    <Info       size={16} strokeWidth={2.5} />,
}

function ToastItem({ id, msg, type }) {
  const remove = useToastStore(s => s.remove)

  useEffect(() => {
    const t = setTimeout(() => remove(id), 3800)
    return () => clearTimeout(t)
  }, [id, remove])

  return (
    <div className={`${styles.toast} ${styles[type]}`} role="alert" aria-live="polite">
      {ICONS[type]}
      <span className={styles.msg}>{msg}</span>
      <button className={styles.close} onClick={() => remove(id)} aria-label="Dismiss">
        <X size={12} strokeWidth={2.5} />
      </button>
    </div>
  )
}

export default function Toast() {
  const toasts = useToastStore(s => s.toasts)
  return (
    <div className={styles.container} aria-live="polite" aria-atomic="false">
      {toasts.map(t => <ToastItem key={t.id} {...t} />)}
    </div>
  )
}
