import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { createClient, publicClient } from '../api.js'
import Brand from '../components/Brand.jsx'
import CustomerChat from '../components/CustomerChat.jsx'
import StaffConsole from '../components/StaffConsole.jsx'
import ThemeToggle from '../components/ThemeToggle.jsx'

const STORAGE_KEY = 'tendline-demo'

function storedTokens() {
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY))
  } catch {
    return null
  }
}

async function loadTokens() {
  const stored = storedTokens()
  if (stored && await createClient(stored.staff).get('/me').catch(() => null)) return stored
  const pair = await publicClient.post('/demo/pair')
  const tokens = { customer: pair.customer.token, staff: pair.staff.token }
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(tokens))
  } catch {
    // Without storage a reload just starts a new workspace.
  }
  return tokens
}

let pendingTokens = null

/** Reuses this tab's private workspace, so a reload doesn't lose the chat or use up the limit. */
function demoTokens() {
  pendingTokens ??= loadTokens().finally(() => { pendingTokens = null })
  return pendingTokens
}

export default function Demo({ onSignOut }) {
  const [tokens, setTokens] = useState(null)
  const [error, setError] = useState('')
  const [pane, setPane] = useState('customer')
  const [activity, setActivity] = useState(0)

  useEffect(() => { document.title = 'Live demo — Tendline' }, [])

  useEffect(() => {
    let current = true
    demoTokens()
      .then((next) => { if (current) setTokens(next) })
      .catch((err) => { if (current) setError(err.status === 429 ? err.message : 'The demo is not available right now.') })
    return () => { current = false }
  }, [])

  const clients = useMemo(
    () => tokens && { customer: createClient(tokens.customer), staff: createClient(tokens.staff) },
    [tokens],
  )

  function signOut() {
    try {
      sessionStorage.removeItem(STORAGE_KEY)
    } catch {
      // Nothing was stored, so there is nothing to clear.
    }
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
