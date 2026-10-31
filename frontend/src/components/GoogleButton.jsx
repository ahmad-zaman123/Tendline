import { useEffect, useRef, useState } from 'react'

const CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID
const SCRIPT_SRC = 'https://accounts.google.com/gsi/client'

function loadScript() {
  if (window.google?.accounts?.id) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${SCRIPT_SRC}"]`)
    if (existing) {
      existing.addEventListener('load', resolve)
      existing.addEventListener('error', reject)
      return
    }
    const s = document.createElement('script')
    s.src = SCRIPT_SRC
    s.async = true
    s.defer = true
    s.onload = resolve
    s.onerror = reject
    document.head.appendChild(s)
  })
}

function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path
        fill="#EA4335"
        d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.5 5.4 2.6 13.2l7.9 6.1C12.4 13.4 17.7 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.5 24.5c0-1.6-.1-3.1-.4-4.5H24v8.5h12.7c-.6 2.9-2.2 5.4-4.7 7.1l7.6 5.9c4.4-4.1 6.9-10.1 6.9-17z"
      />
      <path fill="#FBBC05" d="M10.5 28.7a14.5 14.5 0 0 1 0-9.4l-7.9-6.1a24 24 0 0 0 0 21.6l7.9-6.1z" />
      <path
        fill="#34A853"
        d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.6-5.9c-2.1 1.4-4.9 2.3-8.3 2.3-6.3 0-11.6-3.9-13.5-9.4l-7.9 6.1C6.5 42.6 14.6 48 24 48z"
      />
    </svg>
  )
}

/**
 * "Continue with Google", styled like the rest of the form. Renders nothing unless
 * VITE_GOOGLE_CLIENT_ID is set. Google's own button sits invisibly on top of ours and
 * takes the click, so the sign-in popup and ID token come from Google Identity Services.
 * `onCredential(idToken)` receives the token to send to the backend.
 */
export default function GoogleButton({ onCredential, onError, busy = false }) {
  const holder = useRef(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!CLIENT_ID || !holder.current) return undefined
    let cancelled = false
    loadScript()
      .then(() => {
        if (cancelled || !holder.current) return
        window.google.accounts.id.initialize({
          client_id: CLIENT_ID,
          callback: (response) => onCredential(response.credential),
          ux_mode: 'popup',
        })
        holder.current.innerHTML = ''
        window.google.accounts.id.renderButton(holder.current, {
          type: 'standard',
          theme: 'outline',
          size: 'large',
          text: 'continue_with',
          width: Math.min(400, holder.current.parentElement?.offsetWidth || 320),
        })
        setReady(true)
      })
      .catch(() => {
        setFailed(true)
        onError?.('Google sign-in could not load. Check your connection or use a password.')
      })
    return () => {
      cancelled = true
    }
  }, [onCredential, onError])

  if (!CLIENT_ID || failed) return null
  return (
    <div className="google-signin">
      <div className={'google-wrap' + (ready ? '' : ' is-loading') + (busy ? ' is-busy' : '')} aria-busy={busy}>
        <div className="btn outline google-btn" aria-hidden="true">
          <GoogleMark />
          {busy ? 'Signing you in…' : 'Continue with Google'}
        </div>
        <div ref={holder} className="google-overlay" aria-label="Continue with Google" />
      </div>
      <div className="google-or">
        <span>or with a password</span>
      </div>
    </div>
  )
}
