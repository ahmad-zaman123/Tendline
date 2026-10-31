import { useEffect, useRef } from 'react'

function Source({ source }) {
  const title = source.source.split('#')[1] || source.source
  const body = source.text.split('\n').slice(1).join('\n') || source.text
  return (
    <details className="source">
      <summary>{title}</summary>
      <p>{body}</p>
    </details>
  )
}

/** The conversation as a log, with every entry marked by who handled it. */
export default function TicketLog({ messages, viewer, steps = [], label }) {
  const scroller = useRef(null)
  const authors = {
    user: viewer === 'staff' ? 'Customer' : 'You',
    assistant: 'AI assistant',
    staff: viewer === 'staff' ? 'You, support team' : 'Support team',
  }

  useEffect(() => {
    if (scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight
  }, [messages.length, steps.length])

  return (
    <div className="log" ref={scroller} aria-label={label}>
      {messages.map((message, index) =>
        message.role === 'event' ? (
          <p key={index} className="log-event">{message.text}</p>
        ) : (
          <article key={index} className={`entry entry-${message.role}`}>
            <h3 className="entry-author">{authors[message.role]}</h3>
            <p className="entry-text">{message.text}</p>
            {message.sources?.length > 0 && (
              <div className="sources">
                <span>Answered from</span>
                {message.sources.map((source) => <Source key={source.source} source={source} />)}
              </div>
            )}
          </article>
        ),
      )}
      {steps.length > 0 && (
        <article className="entry entry-assistant">
          <h3 className="entry-author">AI assistant</h3>
          <ol className="steps" role="status">
            {steps.map((step, index) => (
              <li key={step} className={index === steps.length - 1 ? 'is-current' : ''}>{step}</li>
            ))}
          </ol>
        </article>
      )}
    </div>
  )
}
