import { useEffect, useMemo, useState } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { createClient, getStoredToken, storeToken } from './api.js'
import Landing from './pages/Landing.jsx'
import Login from './pages/Login.jsx'

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

  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login user={user} onSignIn={signIn} home={user && homeFor(user)} />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
