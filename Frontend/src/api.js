const TOKEN_KEY = 'tendline-token'
// Empty locally, where Vite proxies /api to the backend.
const API_URL = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '')

export function getStoredToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function storeToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // Without storage the session just ends on reload.
  }
}

const GENERIC_ERROR = 'Something went wrong. Please try again.'
const FIELD_ERRORS = {
  email: 'Please enter a valid email address.',
  name: 'Please enter your name.',
  password: 'Passwords must be between 8 and 200 characters.',
  new_password: 'Passwords must be between 8 and 200 characters.',
  message: 'Please enter a message of up to 2,000 characters.',
}

function failureMessage(detail) {
  if (typeof detail === 'string') return detail
  const field = Array.isArray(detail) ? detail[0]?.loc?.at(-1) : null
  return FIELD_ERRORS[field] || GENERIC_ERROR
}

async function failure(response) {
  const data = await response.json().catch(() => null)
  const error = new Error(failureMessage(data?.detail))
  error.status = response.status
  return error
}

/**
 * An API client bound to one session, so the split-screen demo can hold two at once.
 * `onExpired` runs when the server stops accepting the session's token.
 */
export function createClient(token, onExpired) {
  async function request(path, { method = 'GET', body } = {}) {
    const headers = {}
    if (token) headers.Authorization = `Bearer ${token}`
    if (body) headers['Content-Type'] = 'application/json'
    let response
    try {
      response = await fetch(`${API_URL}/api${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined })
    } catch {
      throw new Error("Can't reach the server. Check your connection and try again.")
    }
    if (response.status === 401 && token) onExpired?.()
    return response
  }

  async function json(path, options) {
    const response = await request(path, options)
    if (!response.ok) throw await failure(response)
    return response.json()
  }

  return {
    get: (path) => json(path),
    post: (path, body) => json(path, { method: 'POST', body }),

    /** Sends a chat message, calling onStep for each real agent step, and returns the result. */
    async chat(body, onStep) {
      const response = await request('/chat/stream', { method: 'POST', body })
      if (!response.ok) throw await failure(response)
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      for (;;) {
        const { value, done } = await reader.read().catch(() => ({ done: true }))
        if (done) throw new Error('The connection dropped. Please try again.')
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()
        for (const line of lines) {
          const event = JSON.parse(line)
          if (event.type === 'step') onStep(event.label)
          if (event.type === 'done') return event
          if (event.type === 'error') throw new Error(event.detail)
        }
      }
    },
  }
}

export const publicClient = createClient(null)
