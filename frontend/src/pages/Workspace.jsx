import { useEffect } from 'react'
import Brand from '../components/Brand.jsx'
import CustomerChat from '../components/CustomerChat.jsx'
import StaffConsole from '../components/StaffConsole.jsx'
import ThemeToggle from '../components/ThemeToggle.jsx'

export default function Workspace({ client, user, onSignOut }) {
  const staff = user.role === 'staff'

  useEffect(() => { document.title = `${staff ? 'Support queue' : 'Support'} — Deskhand` }, [staff])

  return (
    <div className="app-shell">
      <header className="topbar">
        <Brand />
        <div className="topbar-right">
          <span className="who">{user.name}</span>
          <button type="button" className="link-btn" onClick={onSignOut}>Sign out</button>
          <ThemeToggle />
        </div>
      </header>
      <main className="workspace">
        {staff ? <StaffConsole client={client} /> : <CustomerChat client={client} />}
      </main>
    </div>
  )
}
