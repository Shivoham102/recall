'use client'
import { useEffect, useRef, type ReactNode } from 'react'

const svgProps = {
  width: 22,
  height: 22,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
}

const ICONS: Record<string, ReactNode> = {
  sun: (
    <svg {...svgProps}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
    </svg>
  ),
  mic: (
    <svg {...svgProps}>
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" x2="12" y1="19" y2="22" />
    </svg>
  ),
  bell: (
    <svg {...svgProps}>
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
    </svg>
  ),
  mail: (
    <svg {...svgProps}>
      <rect width="20" height="16" x="2" y="4" rx="2" />
      <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
    </svg>
  ),
  radar: (
    <svg {...svgProps}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
    </svg>
  ),
  sparkles: (
    <svg {...svgProps}>
      <path d="M12 3l1.6 4.8a3 3 0 0 0 1.9 1.9L20 11.3l-4.5 1.5a3 3 0 0 0-1.9 1.9L12 19.6l-1.6-4.9a3 3 0 0 0-1.9-1.9L4 11.3l4.5-1.5a3 3 0 0 0 1.9-1.9z" />
      <path d="M19 4v3M20.5 5.5h-3" />
    </svg>
  ),
}

const FEATURES = [
  {
    icon: 'sun',
    title: 'Morning Brief',
    desc: 'Wake up to what matters. AI-curated email, calendar, and tasks. No noise, no newsletters.',
  },
  {
    icon: 'mic',
    title: 'Voice Input',
    desc: 'Talk to it. No typing required. Records, understands, and stores what you say.',
  },
  {
    icon: 'bell',
    title: 'Smart Reminders',
    desc: 'Set it and forget it. Recall nudges you at exactly the right moment.',
  },
  {
    icon: 'mail',
    title: 'Email Intelligence',
    desc: 'Surfaces real person-to-person emails and saves personalized drafts when you ask.',
  },
  {
    icon: 'radar',
    title: 'Follow-up Radar',
    desc: 'Scans your sent mail. Reminds you before you drop the ball on commitments.',
  },
  {
    icon: 'sparkles',
    title: 'Learns Your Patterns',
    desc: 'Gets smarter with use. Adapts to what you actually care about over time.',
  },
]

export default function FeatureCards() {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const cards = containerRef.current?.querySelectorAll<HTMLElement>('.card')
    if (!cards) return

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add('visible')
            observer.unobserve(e.target)
          }
        })
      },
      { threshold: 0.12 }
    )

    cards.forEach((card, i) => {
      card.style.transitionDelay = `${i * 0.08}s`
      observer.observe(card)
    })

    return () => observer.disconnect()
  }, [])

  return (
    <div className="cards" ref={containerRef}>
      {FEATURES.map((f) => (
        <div key={f.title} className="card">
          <span className="card-icon">{ICONS[f.icon]}</span>
          <h3>{f.title}</h3>
          <p>{f.desc}</p>
        </div>
      ))}
    </div>
  )
}
