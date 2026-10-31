const TOKEN_KEY = 'tendline-token'

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

async function failure(response) {
  const data = await response.json().catch(() => null)
  const error = new Error(typeof data?.detail === 'string' ? data.detail : 'Something went wrong. Please try again.')
  error.status = response.status
  return error
}

/** An API client bound to one session, so the split-screen demo can hold two at once. */
export function createClient(token) {
  function request(path, { method = 'GET', body } = {}) {
    const headers = {}
    if (token) headers.Authorization = `Bearer ${token}`
    if (body) headers['Content-Type'] = 'application/json'
    return fetch(`/api${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined })
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
        const { value, done } = await reader.read()
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
