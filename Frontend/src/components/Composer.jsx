import { useEffect, useRef } from 'react'

export default function Composer({ id, label, placeholder, action, hint, value, onChange, onSubmit, disabled }) {
  const input = useRef(null)

  // Grow with the content, up to a few lines.
  useEffect(() => {
    const element = input.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 140)}px`
  }, [value])

  function onKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      onSubmit()
    }
  }

  return (
    <form className="composer" onSubmit={(event) => { event.preventDefault(); onSubmit() }}>
      <label className="sr-only" htmlFor={id}>{label}</label>
      <div className="composer-row">
        <textarea
          id={id}
          ref={input}
          rows={1}
          maxLength={2000}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder}
        />
        <button className="btn primary" type="submit" disabled={disabled || !value.trim()}>{action}</button>
      </div>
      {hint && <p className="composer-hint">{hint}</p>}
    </form>
  )
}
