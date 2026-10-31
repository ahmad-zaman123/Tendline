import { useState } from 'react'

const SUN = 'M12 4V2m0 20v-2m8-8h2M2 12h2m13.7-5.7l1.4-1.4M4.9 19.1l1.4-1.4m0-11.4L4.9 4.9m14.2 14.2l-1.4-1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z'
const MOON = 'M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z'

export default function ThemeToggle() {
  // index.html sets data-theme before first paint.
  const [mode, setMode] = useState(() => document.documentElement.getAttribute('data-theme') || 'light')

  function toggle() {
    const next = mode === 'dark' ? 'light' : 'dark'
    setMode(next)
    try {
      localStorage.setItem('theme', next)
    } catch {
      // In private mode the choice just won't persist.
    }
    document.documentElement.setAttribute('data-theme', next)
  }

  const label = mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'
  return (
    <button type="button" className="theme-toggle" onClick={toggle} aria-label={label} title={label}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d={mode === 'dark' ? SUN : MOON} />
      </svg>
    </button>
  )
}
