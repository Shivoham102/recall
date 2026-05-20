'use client'
import { useEffect, useRef } from 'react'

const FEATURES = [
  {
    icon: '🌞',
    title: 'Morning Brief',
    desc: 'Wake up to what matters. AI-curated email, calendar, and tasks. No noise, no newsletters.',
  },
  {
    icon: '🎙️',
    title: 'Voice Input',
    desc: 'Talk to it. No keyboard required. Records, understands, and stores what you say.',
  },
  {
    icon: '⏱️',
    title: 'Smart Reminders',
    desc: 'Set it and forget it. Recall nudges you at exactly the right moment.',
  },
  {
    icon: '💌',
    title: 'Email Intelligence',
    desc: 'Surfaces real person-to-person emails and saves personalized drafts when you ask.',
  },
  {
    icon: '📡',
    title: 'Follow-up Radar',
    desc: 'Scans your sent mail. Reminds you before you drop the ball on commitments.',
  },
  {
    icon: '🧠',
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
          <span className="card-icon" aria-hidden="true">
            {f.icon}
          </span>
          <h3>{f.title}</h3>
          <p>{f.desc}</p>
        </div>
      ))}
    </div>
  )
}
