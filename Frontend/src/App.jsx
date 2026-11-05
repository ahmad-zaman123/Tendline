import { useEffect, useMemo, useState } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { createClient, getStoredToken, storeToken } from './api.js'
import Demo from './pages/Demo.jsx'
import ForgotPassword from './pages/ForgotPassword.jsx'
import Landing from './pages/Landing.jsx'
import Login from './pages/Login.jsx'
import ResetPassword from './pages/ResetPassword.jsx'
import Workspace from './pages/Workspace.jsx'

const homeFor = (user) => (user.role === 'staff' ? '/staff' : '/chat')

export default function App() {
  const navigate = useNavigate()
  const [token, setToken] = useState(getStoredToken)
  const [user, setUser] = useState(null)
  const [checking, setChecking] = useState(Boolean(token))
  const [unreachable, setUnreachable] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [notice, setNotice] = useState('')
  const client = useMemo(
    () => createClient(token, () => {
      signOut()
      setNotice('Your session has expired. Please sign in again.')
    }),
    [token],
  )

  useEffect(() => {
    if (!token || user) return undefined
    let current = true
    setChecking(true)
    setUnreachable(false)
    client
      .get('/me')
      .then((me) => { if (current) setUser(me) })
      // Only the server can end a session. A failed request just means we couldn't ask it.
      .catch((err) => { if (current && err.status !== 401) setUnreachable(true) })
      .finally(() => { if (current) setChecking(false) })
    return () => { current = false }
  }, [token, attempt])

  function signIn(session) {
    storeToken(session.token)
    setToken(session.token)
    setUser(session.user)
    setChecking(false)
    setUnreachable(false)
    setNotice('')
    navigate(homeFor(session.user))
  }

  /** A password change ends older sessions, so the page carries on with the token it returned. */
  function renewSession(session) {
    storeToken(session.token)
    setToken(session.token)
    setUser(session.user)
  }

  function signOut() {
    storeToken(null)
    setToken(null)
    setUser(null)
    setChecking(false)
    setUnreachable(false)
    setNotice('')
    navigate('/login')
  }

  const switchView = () => client.post('/me/switch').then(signIn)

  function gated(role) {
    if (checking) return null
    if (unreachable) {
      return (
        <main className="auth-screen">
          <div className="auth-card">
            <h1>Can&apos;t reach the server.</h1>
            <p className="auth-subtitle">You&apos;re still signed in. Check your connection and try again.</p>
            <button type="button" className="btn primary" onClick={() => setAttempt((count) => count + 1)}>Try again</button>
          </div>
        </main>
      )
    }
    if (!user) return <Navigate to="/login" replace />
    if (user.role !== role) return <Navigate to={homeFor(user)} replace />
    return (
      <Workspace
        client={client}
        user={user}
        onSignOut={signOut}
        onSwitchView={switchView}
        onSessionRenewed={renewSession}
      />
    )
  }

  return (
    <Routes>
      <Route path="/" element={<Landing user={user} checking={checking} home={user && homeFor(user)} onSignOut={signOut} />} />
      <Route path="/demo" element={<Demo onSignOut={signOut} />} />
      <Route path="/login" element={<Login user={user} notice={notice} onSignIn={signIn} home={user && homeFor(user)} />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/chat" element={gated('customer')} />
      <Route path="/staff" element={gated('staff')} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
