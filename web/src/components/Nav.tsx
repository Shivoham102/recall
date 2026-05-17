import Link from 'next/link'
import LogoIcon from './LogoIcon'

export default function Nav() {
  return (
    <nav>
      <Link href="/" className="nav-logo">
        <LogoIcon size={28} />
        <span className="wordmark">
          recall<span className="cursor">_</span>
        </span>
      </Link>
      <ul className="nav-links">
        <li>
          <Link href="/privacy">Privacy</Link>
        </li>
        <li>
          <Link href="/terms">Terms</Link>
        </li>
        <li>
          <a
            href="https://github.com/Shivoham102/recall"
            target="_blank"
            rel="noopener noreferrer"
          >
            GitHub
          </a>
        </li>
      </ul>
    </nav>
  )
}
