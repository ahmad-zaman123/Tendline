import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { publicClient } from '../api.js'
import Brand from '../components/Brand.jsx'
import ThemeToggle from '../components/ThemeToggle.jsx'

export default function ResetPassword() {
  const [params] = useSearchParams()
  const token = params.get('token') || ''
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [done, setDone] = useState(false)
  const [error, setError] = useState(token ? '' : 'This reset link is invalid or has expired.')
  const [busy, setBusy] = useState(false)

  useEffect(() => { document.title = 'Choose a new password — Tendline' }, [])

  async function submit(event) {
    event.preventDefault()
    if (password !== confirm) {
      setError("The passwords don't match.")
      return
    }
    setBusy(true)
    setError('')
    try {
      await publicClient.post('/password/reset', { token, new_password: password })
      setDone(true)
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
          <h1>{done ? 'Password changed.' : 'Choose a new password.'}</h1>
          {done ? (
            <p className="auth-subtitle" role="status">You can now sign in with it. Any other devices have been signed out.</p>
          ) : (
            <>
              <label className="field">
                <span>New password</span>
                <input type="password" required minLength={8} maxLength={200} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" />
              </label>
              <label className="field">
                <span>Confirm new password</span>
                <input type="password" required minLength={8} maxLength={200} autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
              </label>
              {error && <div className="error-banner" role="alert">{error}</div>}
              <button className="btn primary" type="submit" disabled={busy || !token}>{busy ? 'One moment…' : 'Change password'}</button>
            </>
          )}
          <div className="auth-alt">
            {done
              ? <Link to="/login" className="link-btn">Go to sign in</Link>
              : <Link to="/forgot-password" className="link-btn">Send a new reset link</Link>}
          </div>
        </form>
      </main>
    </div>
  )
}
