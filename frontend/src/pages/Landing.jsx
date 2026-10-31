import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import Brand from '../components/Brand.jsx'
import ThemeToggle from '../components/ThemeToggle.jsx'

export default function Landing() {
  useEffect(() => { document.title = 'Deskhand — AI support with a person one step away' }, [])

  return (
    <div className="landing">
      <header className="landing-nav">
        <Brand />
        <nav className="landing-nav-links" aria-label="Primary">
          <Link to="/login" className="link-btn">Sign in</Link>
          <ThemeToggle />
        </nav>
      </header>

      <main className="landing-main">
        <div>
          <h1 className="landing-title">AI support that knows when to hand off.</h1>
          <p className="landing-lede">
            Deskhand answers from your help docs, checks real orders and passes anything it shouldn&apos;t
            handle to your team, with the full history attached.
          </p>
          <div className="landing-cta">
            <Link to="/demo" className="btn primary">Try the live demo</Link>
            <Link to="/login" className="btn outline">Sign in</Link>
          </div>
          <dl className="landing-steps">
            <div><dt>Answers</dt><dd>From your docs only, showing the passage it used.</dd></div>
            <div><dt>Checks</dt><dd>Orders for the signed-in customer, and nobody else.</dd></div>
            <div><dt>Hands off</dt><dd>Refunds, cancellations, unknowns and outages go to a person.</dd></div>
          </dl>
        </div>

        <figure className="specimen">
          <figcaption className="specimen-head"><span>Ticket from Northwind Desks</span><span>Example</span></figcaption>
          <article className="entry entry-user">
            <h3 className="entry-author">Customer</h3>
            <p className="entry-text">Can I return my desk? I&apos;d like a refund.</p>
          </article>
          <article className="entry entry-assistant">
            <h3 className="entry-author">AI assistant</h3>
            <p className="entry-text">Unused items can be returned within 30 days of delivery. I&apos;ve passed your refund to our support team.</p>
            <div className="sources"><span>Answered from</span><span className="source-static">Returns</span></div>
          </article>
          <p className="log-event">Handed to the support team, with the history attached</p>
          <article className="entry entry-staff">
            <h3 className="entry-author">Support team</h3>
            <p className="entry-text">Your refund is approved. It should reach you within 5 business days.</p>
          </article>
        </figure>
      </main>
    </div>
  )
}
