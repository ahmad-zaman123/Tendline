import { useEffect } from 'react'
import Brand from '../components/Brand.jsx'
import CustomerChat from '../components/CustomerChat.jsx'
import ProfileMenu from '../components/ProfileMenu.jsx'
import StaffConsole from '../components/StaffConsole.jsx'
import ThemeToggle from '../components/ThemeToggle.jsx'

export default function Workspace({ client, user, onSignOut, onSwitchView, onUserChanged }) {
  const staff = user.role === 'staff'

  useEffect(() => { document.title = `${staff ? 'Support queue' : 'Support'} — Tendline` }, [staff])

  return (
    <div className="app-shell">
      <header className="topbar">
        <Brand />
        <div className="topbar-right">
          <ThemeToggle />
          <ProfileMenu
            user={user}
            onSignOut={onSignOut}
            onSwitchView={onSwitchView}
            changePassword={(payload) => client.post('/me/password', payload)}
            onPasswordChanged={onUserChanged}
          />
        </div>
      </header>
      <main className="workspace">
        {staff ? <StaffConsole client={client} /> : <CustomerChat client={client} />}
      </main>
    </div>
  )
}
