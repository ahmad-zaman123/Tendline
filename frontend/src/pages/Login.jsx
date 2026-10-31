import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { publicClient } from '../api.js'
import Brand from '../components/Brand.jsx'
import GoogleButton from '../components/GoogleButton.jsx'
import ThemeToggle from '../components/ThemeToggle.jsx'

export default function Login({ user, home, onSignIn }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { document.title = 'Sign in — Deskhand' }, [])

  const onGoogleCredential = useCallback((credential) => {
    run(() => publicClient.post('/auth/google', { credential }))
  }, [])

  if (user) return <Navigate to={home} replace />

  async function run(request) {
    setBusy(true)
    setError('')
    try {
      onSignIn(await request())
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  function submit(event) {
    event.preventDefault()
    run(() => publicClient.post('/login', { email, password }))
  }

  const demo = (role) => run(() => publicClient.post('/demo/session', { role }))

  return (
    <div className="auth-page">
      <header className="landing-nav">
        <Brand />
        <nav className="landing-nav-links" aria-label="Primary"><ThemeToggle /></nav>
      </header>
      <main className="auth-screen">
        <form className="auth-card" onSubmit={submit}>
          <h1>Back to the desk.</h1>
          <p className="auth-subtitle">Sign in to your support conversations.</p>
          <GoogleButton onCredential={onGoogleCredential} onError={setError} busy={busy} />
          <label className="field">
            <span>Email</span>
            <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
          </label>
          <label className="field">
            <span>Password</span>
            <input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error && <div className="error-banner" role="alert">{error}</div>}
          <button className="btn primary" type="submit" disabled={busy}>{busy ? 'One moment…' : 'Sign in'}</button>
          <div className="auth-alt">
            <span>Just looking around?</span>
            <button type="button" className="link-btn" onClick={() => demo('customer')} disabled={busy}>Try it as a customer</button>
            <button type="button" className="link-btn" onClick={() => demo('staff')} disabled={busy}>Try it as a support agent</button>
            <Link to="/demo" className="link-btn">See both side by side</Link>
          </div>
        </form>
      </main>
    </div>
  )
}
