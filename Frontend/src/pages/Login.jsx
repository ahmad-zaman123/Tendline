import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useLocation } from 'react-router-dom'
import { publicClient } from '../api.js'
import Brand from '../components/Brand.jsx'
import GoogleButton from '../components/GoogleButton.jsx'
import ThemeToggle from '../components/ThemeToggle.jsx'

export default function Login({ user, notice, home, onSignIn }) {
  const { state } = useLocation()
  const [isRegister, setIsRegister] = useState(Boolean(state?.register))
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { document.title = `${isRegister ? 'Sign up' : 'Sign in'} — Tendline` }, [isRegister])

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
    run(() => (isRegister
      ? publicClient.post('/register', { email, name, password })
      : publicClient.post('/login', { email, password })))
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
          <h1>{isRegister ? 'Set up your desk.' : 'Back to the desk.'}</h1>
          <p className="auth-subtitle">
            {isRegister
              ? 'Your own account, with sample orders and a private support view to try hand-offs.'
              : 'Sign in to your support conversations.'}
          </p>
          <GoogleButton onCredential={onGoogleCredential} onError={setError} busy={busy} />
          {isRegister && (
            <label className="field">
              <span>Full name</span>
              <input type="text" required maxLength={100} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Ada Lovelace" />
            </label>
          )}
          <label className="field">
            <span>Email</span>
            <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              type="password"
              required
              minLength={isRegister ? 8 : undefined}
              maxLength={200}
              autoComplete={isRegister ? 'new-password' : 'current-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={isRegister ? 'At least 8 characters' : undefined}
            />
          </label>
          {!isRegister && <Link to="/forgot-password" className="link-btn auth-forgot">Forgot password?</Link>}
          {notice && !error && <div className="error-banner" role="status">{notice}</div>}
          {error && <div className="error-banner" role="alert">{error}</div>}
          <button className="btn primary" type="submit" disabled={busy}>{busy ? 'One moment…' : isRegister ? 'Create account' : 'Sign in'}</button>
          <div className="auth-alt">
            <button type="button" className="link-btn" onClick={() => { setError(''); setIsRegister(!isRegister) }}>
              {isRegister ? 'Already have an account? Sign in' : 'New here? Create an account'}
            </button>
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
