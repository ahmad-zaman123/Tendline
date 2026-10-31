import { useEffect, useRef, useState } from 'react'

function EyeIcon({ off }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" aria-hidden="true">
      <path
        d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.6" />
      {off && <path d="M4 4l16 16" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />}
    </svg>
  )
}

function PasswordField({ label, value, onChange, autoComplete, autoFocus, inputRef }) {
  const [show, setShow] = useState(false)
  return (
    <label className="pwfield">
      <span>{label}</span>
      <div className="pwwrap">
        <input
          ref={inputRef}
          type={show ? 'text' : 'password'}
          required
          minLength={8}
          autoComplete={autoComplete}
          autoFocus={autoFocus}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          className="pwreveal"
          onClick={() => setShow((v) => !v)}
          aria-label={show ? 'Hide password' : 'Show password'}
          title={show ? 'Hide password' : 'Show password'}
        >
          <EyeIcon off={show} />
        </button>
      </div>
    </label>
  )
}

/**
 * Modal for changing the password. Accounts that signed up with Google have none yet,
 * so `hasPassword={false}` hides the current-password field and the copy says "set".
 */
export default function ChangePasswordDialog({ hasPassword: hasPasswordNow, changePassword, onClose, onChanged }) {
  // Frozen at open time: once a first password is saved the parent refreshes the user,
  // and the dialog should keep showing the "set" wording and success message.
  const [hasPassword] = useState(hasPasswordNow)
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const first = useRef(null)

  useEffect(() => {
    first.current?.focus()
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const mismatch = confirm.length > 0 && confirm !== next
  const canSubmit = next.length >= 8 && confirm === next && (!hasPassword || current.length > 0) && !busy

  async function handleSubmit(e) {
    e.preventDefault()
    if (!canSubmit) return
    setError('')
    setBusy(true)
    try {
      await changePassword(hasPassword ? { current_password: current, new_password: next } : { new_password: next })
      setDone(true)
      onChanged?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="pmodal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="pmodal" role="dialog" aria-modal="true" aria-labelledby="pw-title" onSubmit={handleSubmit}>
        <h2 id="pw-title">{hasPassword ? 'Change password' : 'Set a password'}</h2>
        {done ? (
          <>
            <p className="pmodal-hint">
              {hasPassword
                ? 'Your password has been changed.'
                : 'You can now sign in with your email and this password, as well as with Google.'}
            </p>
            <div className="pmodal-actions">
              <button type="button" className="btn primary" onClick={onClose}>
                Done
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="pmodal-hint">
              {hasPassword
                ? 'Pick something at least 8 characters long.'
                : 'Your account was created with Google and has no password yet. Add one to sign in without Google too.'}
            </p>
            {hasPassword && (
              <PasswordField
                label="Current password"
                value={current}
                onChange={setCurrent}
                autoComplete="current-password"
                inputRef={first}
              />
            )}
            <PasswordField
              label="New password"
              value={next}
              onChange={setNext}
              autoComplete="new-password"
              inputRef={hasPassword ? undefined : first}
            />
            <PasswordField
              label="Confirm new password"
              value={confirm}
              onChange={setConfirm}
              autoComplete="new-password"
            />
            {mismatch && <div className="pmodal-note">The two passwords do not match yet.</div>}
            {error && (
              <div className="error-banner" role="alert">
                {error}
              </div>
            )}
            <div className="pmodal-actions">
              <button type="button" className="btn outline" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button type="submit" className="btn primary" disabled={!canSubmit}>
                {busy ? 'Saving…' : hasPassword ? 'Change password' : 'Set password'}
              </button>
            </div>
          </>
        )}
      </form>
    </div>
  )
}
