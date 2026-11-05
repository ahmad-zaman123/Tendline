import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { createClient } from '../api.js'
import { clearDemoTokens, demoErrorMessage, demoTokens } from '../demoSession.js'
import Brand from '../components/Brand.jsx'
import CustomerChat from '../components/CustomerChat.jsx'
import StaffConsole from '../components/StaffConsole.jsx'
import ThemeToggle from '../components/ThemeToggle.jsx'

export default function Demo({ onSignOut }) {
  const [tokens, setTokens] = useState(null)
  const [error, setError] = useState('')
  const [pane, setPane] = useState('customer')
  const [activity, setActivity] = useState(0)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => { document.title = 'Live demo — Tendline' }, [])

  useEffect(() => {
    let current = true
    demoTokens()
      .then((next) => { if (current) setTokens(next) })
      .catch((err) => { if (current) setError(demoErrorMessage(err)) })
    return () => { current = false }
  }, [attempt])

  const clients = useMemo(() => {
    if (!tokens) return null
    // An expired demo has nothing worth keeping, so a new one takes its place.
    const restart = () => {
      clearDemoTokens()
      setTokens(null)
      setAttempt((count) => count + 1)
    }
    return { customer: createClient(tokens.customer, restart), staff: createClient(tokens.staff, restart) }
  }, [tokens])

  function signOut() {
    clearDemoTokens()
    onSignOut()
  }

  return (
    <div className="app-shell demo-shell">
      <header className="topbar">
        <Brand />
        <div className="topbar-right">
          {tokens
            ? <button type="button" className="link-btn" onClick={signOut}>Sign out</button>
            : <Link to="/login" className="link-btn">Sign in</Link>}
          <ThemeToggle />
        </div>
      </header>

      <ol className="demo-guide" aria-label="How to try the demo">
        <li><strong>Ask for a refund</strong> as the customer.</li>
        <li><strong>Open the ticket</strong> as the support team and reply.</li>
        <li><strong>Resolve it</strong> and the AI takes back over.</li>
      </ol>

      <div className="pane-tabs" role="group" aria-label="Choose a view">
        <button type="button" aria-pressed={pane === 'customer'} onClick={() => setPane('customer')}>Customer</button>
        <button type="button" aria-pressed={pane === 'staff'} onClick={() => setPane('staff')}>Support team</button>
      </div>

      {error && <div className="error-banner demo-error" role="alert">{error}</div>}
      {!clients && !error && <p className="case-empty">Setting up the demo…</p>}
      {clients && (
        <main className="demo-grid">
          <div className={`demo-pane ${pane === 'customer' ? 'is-visible' : ''}`}>
            <h2 className="demo-pane-title">Customer</h2>
            <CustomerChat client={clients.customer} onActivity={() => setActivity((count) => count + 1)} />
          </div>
          <div className={`demo-pane ${pane === 'staff' ? 'is-visible' : ''}`}>
            <h2 className="demo-pane-title">Support team</h2>
            <StaffConsole client={clients.staff} refreshSignal={activity} />
          </div>
        </main>
      )}
    </div>
  )
}
