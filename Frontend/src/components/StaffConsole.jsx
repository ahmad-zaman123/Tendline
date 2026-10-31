import { useEffect, useState } from 'react'
import Composer from './Composer.jsx'
import TicketLog from './TicketLog.jsx'

const POLL_MS = 4000
const SYSTEM_REASONS = {
  model_error: 'The AI model was unavailable',
  tool_error: 'An AI tool failed',
  empty_model_reply: 'The AI gave no answer',
  step_limit: 'The AI took too many steps',
}

function waited(createdAt, now) {
  const minutes = Math.floor((now - new Date(createdAt).getTime()) / 60000)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  return hours < 24 ? `${hours} hr` : `${Math.floor(hours / 24)} d`
}

function describeCall(call) {
  const { name, args, result } = call
  if (name === 'search_help_docs') {
    const found = (result.passages || []).map((passage) => passage.source.split('#')[1])
    return [`Searched the help docs for “${args.query}”`, found.length ? `Found: ${found.join(', ')}` : 'Found nothing relevant']
  }
  if (name === 'get_order') {
    return [`Looked up order ${args.order_id}`, result.order ? `Status: ${result.order.status}` : "Not on this customer's account"]
  }
  if (name === 'list_my_orders') {
    return ["Listed this customer's orders", `${(result.orders || []).length} found`]
  }
  if (name === 'escalate_to_human') return ['Handed the conversation to the team', args.reason]
  return [name, '']
}

export default function StaffConsole({ client, refreshSignal }) {
  const [queue, setQueue] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState('')
  const [offline, setOffline] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  const selected = queue?.find((item) => item.id === selectedId) || null
  const conversationId = selected?.conversation_id

  useEffect(() => {
    let current = true
    async function refresh() {
      try {
        const items = await client.get('/staff/escalations')
        const open = conversationId ? await client.get(`/staff/conversations/${conversationId}`) : null
        if (!current) return
        setQueue(items)
        setDetail(open)
        setNow(Date.now())
        setOffline(false)
      } catch {
        // The next poll tries again, and clears this once it works.
        if (current) setOffline(true)
      }
    }
    refresh()
    // Hidden tabs stay quiet, so an open tab doesn't keep the database awake.
    const timer = setInterval(() => { if (!document.hidden) refresh() }, POLL_MS)
    return () => {
      current = false
      clearInterval(timer)
    }
  }, [client, conversationId, refreshSignal])

  async function act(action, work) {
    setBusy(action)
    setError('')
    try {
      await work()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(null)
    }
  }

  function reply() {
    const text = draft.trim()
    if (!text || busy) return
    act('reply', async () => {
      await client.post(`/staff/conversations/${conversationId}/reply`, { message: text })
      setDraft('')
      setDetail(await client.get(`/staff/conversations/${conversationId}`))
    })
  }

  function resolve() {
    act('resolve', async () => {
      await client.post(`/staff/escalations/${selected.id}/resolve`)
      setQueue((items) => items.filter((item) => item.id !== selected.id))
      setSelectedId(null)
      setDetail(null)
    })
  }

  return (
    <section className="pane console" aria-label="Support team console">
      <aside className="queue">
        <h2 className="queue-title">
          Waiting on a person <span className="count">{queue ? queue.length : '–'}</span>
        </h2>
        {queue?.length === 0 && (
          <p className="queue-empty">Nothing waiting. Conversations the AI hands off appear here, oldest first.</p>
        )}
        {!queue && !offline && <p className="queue-empty">Loading the queue…</p>}
        {offline && <p className="queue-offline" role="status">Can&apos;t reach the server. Still trying…</p>}
        <ul className="queue-list">
          {queue?.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className={`queue-item ${selectedId === item.id ? 'is-active' : ''}`}
                aria-pressed={selectedId === item.id}
                onClick={() => { setSelectedId(item.id); setDetail(null); setDraft(''); setError('') }}
              >
                <span className="queue-row">
                  <strong>{item.customer_name}</strong>
                  <time dateTime={item.created_at}>{waited(item.created_at, now)}</time>
                </span>
                <span className="queue-reason">{SYSTEM_REASONS[item.reason] || item.reason}</span>
                {item.last_role === 'user' && <span className="status status-waiting">Customer wrote again</span>}
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="case">
        {!selected ? (
          <p className="case-empty">{queue?.length ? 'Select a conversation to read it and reply.' : queue ? 'No conversation selected.' : ''}</p>
        ) : (
          <>
            <header className="case-head">
              <div>
                <h2>{selected.customer_name} <span className="case-email">{selected.customer_email}</span></h2>
                <p>{selected.summary}</p>
              </div>
              <button type="button" className="btn outline small" onClick={resolve} disabled={Boolean(busy)}>
                {busy === 'resolve' ? 'Resolving…' : 'Resolve'}
              </button>
            </header>
            {detail ? (
              <TicketLog messages={detail.messages} viewer="staff" label="Conversation with the customer" />
            ) : (
              <p className="case-empty">Loading the conversation…</p>
            )}
            <div className="composer-bar">
              {error && <div className="error-banner" role="alert">{error}</div>}
              <Composer
                id="staff-reply"
                label="Reply to the customer"
                placeholder="Reply to the customer"
                action="Send"
                value={draft}
                onChange={setDraft}
                onSubmit={reply}
                disabled={Boolean(busy)}
              />
            </div>
            {detail?.tool_calls?.length > 0 && (
              <details className="audit">
                <summary>What the AI did ({detail.tool_calls.length})</summary>
                <ol>
                  {detail.tool_calls.map((call, index) => {
                    const [action, outcome] = describeCall(call)
                    return (
                      <li key={index}>
                        <strong>{action}</strong>
                        {outcome && <span>{outcome}</span>}
                      </li>
                    )
                  })}
                </ol>
              </details>
            )}
          </>
        )}
      </div>
    </section>
  )
}
