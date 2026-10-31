import { useEffect, useRef, useState } from 'react'

import ChangePasswordDialog from './ChangePasswordDialog.jsx'

/* A fixed set of muted hues; the account id picks one, so a person keeps their colour. */
const AVATAR_COLORS = ['#8a5a44', '#5b6b7c', '#7a4e6d', '#4f6f52', '#9b5d2f', '#3f6b78', '#6d5f8e', '#7c6b3d']

function hashOf(text) {
  let h = 0
  for (const ch of String(text)) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return h
}

export function initials(name) {
  const parts = String(name || '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

/** What to call the person: their name, or the part of the email before the @. */
export function displayName(user) {
  const name = (user?.name || '').trim()
  if (name) return name
  return (user?.email || '').split('@')[0] || 'You'
}

export function Avatar({ user, size = 'sm' }) {
  const name = displayName(user)
  return (
    <span
      className={'avatar ' + size}
      style={{ background: AVATAR_COLORS[hashOf(user?.email ?? '') % AVATAR_COLORS.length] }}
      aria-hidden="true"
    >
      {initials(name)}
    </span>
  )
}

function Icon({ d }) {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  )
}
const ICON = {
  key: 'M21 2l-2 2m-7.6 7.6a5.5 5.5 0 1 1-7.8 7.8 5.5 5.5 0 0 1 7.8-7.8zm0 0L19 3m-3.5 3.5L18 9',
  out: 'M10 17l5-5-5-5m5 5H3m9-9h7a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1h-7',
  swap: 'M17 1l4 4-4 4M3 11V9a4 4 0 0 1 4-4h14M7 23l-4-4 4-4M21 13v2a4 4 0 0 1-4 4H3',
}

/**
 * Avatar + first name in the header; opens a menu with the account, Change password and Sign out.
 * Demo accounts have no password, so they only get the note and Sign out.
 * Registered accounts can also switch between their customer view and their own support team view.
 * `changePassword(payload)` posts to the API; `onPasswordChanged` lets the parent refresh the user.
 */
export default function ProfileMenu({ user, onSignOut, onSwitchView, changePassword, onPasswordChanged }) {
  const [open, setOpen] = useState(false)
  const [dialog, setDialog] = useState(false)
  const [switchError, setSwitchError] = useState('')
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    ref.current?.querySelector('[role="menuitem"]')?.focus()
    const onDoc = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false)
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        const items = Array.from(ref.current?.querySelectorAll('[role="menuitem"]') ?? [])
        const i = items.indexOf(document.activeElement)
        const next = e.key === 'ArrowDown' ? (i + 1) % items.length : (i - 1 + items.length) % items.length
        items[next]?.focus()
        e.preventDefault()
      }
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (!user) return null
  const name = displayName(user)

  return (
    <div className="pmenu" ref={ref}>
      <button
        type="button"
        className="ptrigger"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Account menu"
      >
        <Avatar user={user} />
        <span className="pname">{name.split(' ')[0]}</span>
        <svg
          className="caret"
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>
      {open ? (
        <div className="pdrop" role="menu" aria-label="Account">
          <div className="pcardhead">
            <Avatar user={user} size="lg" />
            <div className="pwho">
              <div className="name">{name}</div>
              <div className="hint" title={user.email}>
                {user.email}
              </div>
              <div className="hint">{user.role === 'staff' ? 'Support team' : 'Customer'}</div>
            </div>
          </div>
          <div className="psep" />
          {user.can_switch ? (
            <>
              <button
                type="button"
                role="menuitem"
                className="pitem"
                onClick={() => {
                  setSwitchError('')
                  onSwitchView().then(() => setOpen(false)).catch((err) => setSwitchError(err.message))
                }}
              >
                <Icon d={ICON.swap} />
                {user.role === 'staff' ? 'Back to customer view' : 'Open the support view'}
              </button>
              {switchError ? <p className="pnote" role="alert">{switchError}</p> : null}
              <div className="psep" />
            </>
          ) : null}
          {user.demo ? (
            <p className="pnote">Private demo account. It is deleted after a day.</p>
          ) : user.can_switch && user.role === 'staff' ? null : (
            <>
              <button
                type="button"
                role="menuitem"
                className="pitem"
                onClick={() => {
                  setOpen(false)
                  setDialog(true)
                }}
              >
                <Icon d={ICON.key} />
                {user.has_password === false ? 'Set a password' : 'Change password'}
              </button>
              <div className="psep" />
            </>
          )}
          <button type="button" role="menuitem" className="pitem danger" onClick={onSignOut}>
            <Icon d={ICON.out} />
            Sign out
          </button>
        </div>
      ) : null}
      {dialog ? (
        <ChangePasswordDialog
          hasPassword={user.has_password !== false}
          changePassword={changePassword}
          onClose={() => setDialog(false)}
          onChanged={onPasswordChanged}
        />
      ) : null}
    </div>
  )
}
