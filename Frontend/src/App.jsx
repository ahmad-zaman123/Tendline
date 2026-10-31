import { useEffect, useMemo, useState } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { createClient, getStoredToken, storeToken } from './api.js'
import Demo from './pages/Demo.jsx'
import Landing from './pages/Landing.jsx'
import Login from './pages/Login.jsx'
import Workspace from './pages/Workspace.jsx'

const homeFor = (user) => (user.role === 'staff' ? '/staff' : '/chat')

export default function App() {
  const navigate = useNavigate()
  const [token, setToken] = useState(getStoredToken)
  const [user, setUser] = useState(null)
  const [checking, setChecking] = useState(Boolean(token))
  const client = useMemo(() => createClient(token), [token])

  useEffect(() => {
    if (!token || user) return undefined
    let current = true
    client
      .get('/me')
      .then((me) => { if (current) setUser(me) })
      .catch(() => { if (current) signOut() })
      .finally(() => { if (current) setChecking(false) })
    return () => { current = false }
  }, [token])

  function signIn(session) {
    storeToken(session.token)
    setToken(session.token)
    setUser(session.user)
    setChecking(false)
    navigate(homeFor(session.user))
  }

  function signOut() {
    storeToken(null)
    setToken(null)
    setUser(null)
    setChecking(false)
    navigate('/login')
  }

  function gated(role) {
    if (checking) return null
    if (!user) return <Navigate to="/login" replace />
    if (user.role !== role) return <Navigate to={homeFor(user)} replace />
    return (
      <Workspace
        client={client}
        user={user}
        onSignOut={signOut}
        onUserChanged={() => client.get('/me').then(setUser).catch(() => {})}
      />
    )
  }

  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/demo" element={<Demo />} />
      <Route path="/login" element={<Login user={user} onSignIn={signIn} home={user && homeFor(user)} />} />
      <Route path="/chat" element={gated('customer')} />
      <Route path="/staff" element={gated('staff')} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
