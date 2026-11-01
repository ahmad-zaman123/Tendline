import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { publicClient } from '../api.js'
import Brand from '../components/Brand.jsx'
import ThemeToggle from '../components/ThemeToggle.jsx'

export default function ForgotPassword() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { document.title = 'Reset your password — Tendline' }, [])

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      setSent((await publicClient.post('/password/forgot', { email })).detail)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <header className="landing-nav">
        <Brand />
        <nav className="landing-nav-links" aria-label="Primary"><ThemeToggle /></nav>
      </header>
      <main className="auth-screen">
        <form className="auth-card" onSubmit={submit}>
          <h1>Reset your password.</h1>
          {sent ? (
            <p className="auth-subtitle" role="status">{sent} It expires in 15 minutes, so check your spam folder if you don&apos;t see it.</p>
          ) : (
            <>
              <p className="auth-subtitle">Enter your email and we&apos;ll send you a link to choose a new one.</p>
              <label className="field">
                <span>Email</span>
                <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
              </label>
              {error && <div className="error-banner" role="alert">{error}</div>}
              <button className="btn primary" type="submit" disabled={busy}>{busy ? 'One moment…' : 'Send reset link'}</button>
            </>
          )}
          <div className="auth-alt">
            <Link to="/login" className="link-btn">Back to sign in</Link>
          </div>
        </form>
      </main>
    </div>
  )
}
