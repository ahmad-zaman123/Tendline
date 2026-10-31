import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { createClient, publicClient } from '../api.js'
import Brand from '../components/Brand.jsx'
import CustomerChat from '../components/CustomerChat.jsx'
import StaffConsole from '../components/StaffConsole.jsx'
import ThemeToggle from '../components/ThemeToggle.jsx'

export default function Demo() {
  const [tokens, setTokens] = useState(null)
  const [error, setError] = useState('')
  const [pane, setPane] = useState('customer')
  const [activity, setActivity] = useState(0)

  useEffect(() => { document.title = 'Live demo — Deskhand' }, [])

  useEffect(() => {
    let current = true
    Promise.all(['customer', 'staff'].map((role) => publicClient.post('/demo/session', { role })))
      .then(([customer, staff]) => { if (current) setTokens({ customer: customer.token, staff: staff.token }) })
      .catch(() => { if (current) setError('The demo is not available right now.') })
    return () => { current = false }
  }, [])

  const clients = useMemo(
    () => tokens && { customer: createClient(tokens.customer), staff: createClient(tokens.staff) },
    [tokens],
  )

  return (
    <div className="app-shell demo-shell">
      <header className="topbar">
        <Brand />
        <div className="topbar-right">
          <Link to="/login" className="link-btn">Sign in</Link>
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
