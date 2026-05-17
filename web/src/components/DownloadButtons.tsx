'use client'
import { useEffect, useRef } from 'react'

const FALLBACK = 'https://github.com/Shivoham102/recall/releases/latest'
const API = 'https://api.github.com/repos/Shivoham102/recall/releases/latest'

function WinIcon() {
  return (
    <svg className="dl-icon" width="20" height="20" viewBox="0 0 88 88" fill="white" aria-hidden="true">
      <path d="M0 12.4L35.7 7.6V43H0V12.4zM40 6.9L88 0V43H40V6.9zM0 47H35.7V82.3L0 77.6V47zM40 47H88V88L40 81.2V47z" />
    </svg>
  )
}

function MacIcon() {
  return (
    <svg className="dl-icon" width="20" height="20" viewBox="0 0 24 24" fill="white" aria-hidden="true">
      <path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.8-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83zM13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z" />
    </svg>
  )
}

function LinuxIcon() {
  return (
    <svg className="dl-icon" width="20" height="20" viewBox="0 0 24 24" fill="white" aria-hidden="true">
      <path d="M12.504 0C6.003 0 2.25 4.457 2.25 8.875c0 2.91.987 5.44 2.636 7.017a5.53 5.53 0 0 0-.554 1.832c-.114.845.19 1.7.86 2.343.659.631 1.586.933 2.51.933.347 0 .694-.046 1.03-.138.648-.178 1.275-.517 1.848-.984.44.064.886.096 1.334.096.449 0 .897-.032 1.338-.097.572.468 1.199.807 1.847.985.336.092.683.138 1.029.138.924 0 1.852-.302 2.51-.933.67-.643.975-1.498.86-2.343a5.53 5.53 0 0 0-.553-1.832C20.763 14.315 21.75 11.785 21.75 8.875 21.75 4.457 18.005 0 12.504 0zm0 1.5c5.13 0 7.746 4.003 7.746 7.375 0 2.56-.872 4.81-2.318 6.21.246.58.42 1.216.487 1.877.065.604-.103 1.12-.493 1.493-.4.382-.97.572-1.533.572-.227 0-.455-.03-.676-.09-.51-.14-.994-.41-1.424-.777a9.06 9.06 0 0 1-1.789.177 9.07 9.07 0 0 1-1.788-.177c-.43.368-.915.637-1.425.777-.22.06-.449.09-.676.09-.563 0-1.133-.19-1.533-.572-.39-.374-.558-.89-.493-1.493.067-.661.24-1.298.487-1.878C5.126 13.685 4.254 11.435 4.254 8.875 4.254 5.503 6.875 1.5 12.504 1.5zm-2.4 8.5c-.69 0-1.25.84-1.25 1.875S9.414 13.75 10.104 13.75s1.25-.84 1.25-1.875S10.794 10 10.104 10zm4.8 0c-.69 0-1.25.84-1.25 1.875s.56 1.875 1.25 1.875 1.25-.84 1.25-1.875S15.594 10 14.904 10z" />
    </svg>
  )
}

export default function DownloadButtons() {
  const winRef = useRef<HTMLAnchorElement>(null)
  const macRef = useRef<HTMLAnchorElement>(null)
  const linuxRef = useRef<HTMLAnchorElement>(null)

  useEffect(() => {
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), 5000)

    fetch(API, { signal: ctrl.signal })
      .then((r) => {
        clearTimeout(timer)
        if (!r.ok) return null
        return r.json() as Promise<{ assets?: { browser_download_url: string }[] }>
      })
      .then((d) => {
        if (!d?.assets) return
        for (const a of d.assets) {
          const u = a.browser_download_url
          const ref = u.endsWith('.exe')
            ? winRef
            : u.endsWith('.dmg')
              ? macRef
              : u.endsWith('.AppImage')
                ? linuxRef
                : null
          if (ref?.current) ref.current.href = u
        }
      })
      .catch(() => {})

    return () => {
      clearTimeout(timer)
      ctrl.abort()
    }
  }, [])

  return (
    <div className="dl-row">
      <a
        ref={winRef}
        className="dl-btn"
        href={FALLBACK}
        target="_blank"
        rel="noopener noreferrer"
      >
        <WinIcon />
        <span className="dl-text">
          <span className="dl-label">Download for Windows</span>
          <span className="dl-arch">x64 installer (.exe)</span>
        </span>
      </a>
      <a
        ref={macRef}
        className="dl-btn"
        href={FALLBACK}
        target="_blank"
        rel="noopener noreferrer"
      >
        <MacIcon />
        <span className="dl-text">
          <span className="dl-label">Download for macOS</span>
          <span className="dl-arch">Apple Silicon (.dmg)</span>
        </span>
      </a>
      <a
        ref={linuxRef}
        className="dl-btn"
        href={FALLBACK}
        target="_blank"
        rel="noopener noreferrer"
      >
        <LinuxIcon />
        <span className="dl-text">
          <span className="dl-label">Download for Linux</span>
          <span className="dl-arch">x86_64 (.AppImage)</span>
        </span>
      </a>
    </div>
  )
}
