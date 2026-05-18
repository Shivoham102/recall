import Link from 'next/link'
import LogoIcon from './LogoIcon'

export default function Footer() {
  return (
    <footer>
      <div className="footer-inner">
        <Link href="/" className="footer-logo">
          <LogoIcon size={20} />
          <span className="footer-wordmark">recall</span>
        </Link>
        <ul className="footer-links">
          <li>
            <Link href="/privacy">Privacy Policy</Link>
          </li>
          <li>
            <Link href="/terms">Terms of Service</Link>
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
        <span className="footer-attr">Built by Shivoham Angal</span>
      </div>
    </footer>
  )
}
