import { createClient, publicClient } from './api.js'

const STORAGE_KEY = 'tendline-demo'

function storedTokens() {
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY))
  } catch {
    return null
  }
}

async function loadTokens() {
  const stored = storedTokens()
  if (stored && await createClient(stored.staff).get('/me').catch(() => null)) return stored
  const pair = await publicClient.post('/demo/pair')
  const tokens = { customer: pair.customer.token, staff: pair.staff.token }
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(tokens))
  } catch {
    // Without storage a reload just starts a new workspace.
  }
  return tokens
}

let pendingTokens = null

/** Reuses this tab's private workspace, so a reload doesn't lose the chat or use up the limit. */
export function demoTokens() {
  pendingTokens ??= loadTokens().finally(() => { pendingTokens = null })
  return pendingTokens
}

export function clearDemoTokens() {
  try {
    sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    // Nothing was stored, so there is nothing to clear.
  }
}

export function demoErrorMessage(err) {
  return err?.status === 429 ? err.message : 'The demo is not available right now.'
}
