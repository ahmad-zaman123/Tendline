import { Link } from 'react-router-dom'

function LogoMark() {
  return (
    <svg className="logo-mark" viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true">
      <path d="M3.5 7.5H14a4 4 0 0 1 0 8H9.5" stroke="var(--ai)" strokeWidth="2" strokeLinecap="round" />
      <path d="M12.5 12l-3.5 3.5 3.5 3.5" stroke="var(--person)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export default function Brand() {
  return (
    <Link to="/" className="brand" aria-label="Deskhand home">
      <LogoMark />
      Deskhand
    </Link>
  )
}
